from ..streamerbase import StreamerBase
import os
import uuid
from datetime import datetime, timezone, timedelta
from flask import Response, redirect
from html import escape as _xe
from lib.utils.helpers import load_pickle
from lib.utils.ttlcache import TTLCache


ZATTOOTV_USERAGENT = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/145.0.0.0 Safari/537.36"
ZATTOOTV_COOKIE_FILE = "cache/zattootv/login.pkl"
ZATTOOTV_EPG_DURATION_HOURS = os.getenv("ZATTOOTV_EPG_DURATION_HOURS", 11)

# CACHE DURATIONS
ZATTOOTV_PLAYLIST_CACHE_MINUTES = os.getenv("ZATTOOTV_PLAYLIST_CACHE_MINUTES", 60)
ZATTOOTV_EPG_CACHE_MINUTES = os.getenv("ZATTOOTV_EPG_CACHE_MINUTES", 360)

class ZattooTV(StreamerBase):
    def __init__(self, debug: bool = False, ip: str = "localhost", port: int = 7080, **kwargs):
        super().__init__(ip=ip, port=port, debug=debug, **kwargs)
        self.provider_name = "ZattooTV"
        self.uuid = str(uuid.uuid4())
        self.login_url = "https://zattoo.com/zapi/v3/account/login"
        self.token_url = f"https://zattoo.com/client/token.json?id={self.uuid}"
        self.hello_url = "https://zattoo.com/zapi/v3/session/hello"
        self.channels_url = "https://zattoo.com/zapi/v3/channels"
        self.watch_live_base_url = "https://zattoo.com/zapi/watch/live"
        self.image_base_url = "https://images.zattic.com/cms"
        self.logo_base_url = "https://images.zattic.com/logos"
        self.channels = []
        self._raw_hello_response = None
        self.public_id = None
        self.zpush_url = None
        self.client_app_token = None
        self.beaker_session_id = None
        self.account_info = None
        self.cookies = None
        self.power_guide_hash = None
        self.lineup_hash = None

        # EPG
        # Cache: 6 Stunden (360 Minuten)
        self._epg_cache = TTLCache[str](ttl_minutes=ZATTOOTV_EPG_CACHE_MINUTES)

        # playlist caching (optional, könnte später implementiert werden)
        self._playlist_cache       = TTLCache[str](ttl_minutes=ZATTOOTV_PLAYLIST_CACHE_MINUTES)
        self._playlist_cache_kodi  = TTLCache[str](ttl_minutes=ZATTOOTV_PLAYLIST_CACHE_MINUTES)

    def _base_headers(self, accept: str = "application/json") -> dict:
        return {
            "User-Agent": ZATTOOTV_USERAGENT,
            "accept": accept,
            "accept-language": "de-DE,de;q=0.9",
            "accept-encoding": "gzip,deflate,br,zstd",
            "x-zapi-stage": "production",
            "referer": "https://zattoo.com/client",
            "origin": "https://zattoo.com",
        }

    def _load_cookies(self, cookie_file: str = ZATTOOTV_COOKIE_FILE) -> bool:
        try:
            cookies = load_pickle(cookie_file)
            if not cookies:
                return False
            self.http.cookies.update(cookies)
            self.beaker_session_id = self.http.cookies.get("beaker.session.id")
            return self.beaker_session_id is not None
        except Exception:
            return False

    def _fetch_token(self) -> bool:
        self.http.headers.update(self._base_headers(accept="*/*"))
        resp = self.http.get(self.token_url)
        if not resp.ok:
            self.print(f"Token fetch failed: {resp.status_code}")
            return False
        data = resp.json()
        self.client_app_token = data.get("session_token")
        return bool(self.client_app_token)

    def _fetch_hello(self) -> bool:
        if not self._fetch_token():
            return False
        self.http.headers.update(self._base_headers())
        resp = self.http.post(
            self.hello_url,
            data={
                "uuid": self.uuid,
                "lang": "de",
                "app_version": "3.2609.1",
                "client_app_token": self.client_app_token,
            },
        )
        if not resp.ok:
            self.print(f"Hello failed: {resp.status_code}")
            return False
        data = resp.json()
        self._raw_hello_response = data
        self.account_info = data.get("account")
        self.public_id = (self.account_info or {}).get("public_id")
        self.zpush_url = (self.account_info or {}).get("zpush_url")
        self.power_guide_hash = data.get("power_guide_hash")
        self.lineup_hash = data.get("lineup_hash")
        assets = data.get("assets", {})
        self.image_base_url = assets.get("image_base_url", self.image_base_url)
        self.logo_base_url = assets.get("logo_base_url", self.logo_base_url)
        self.beaker_session_id = self.http.cookies.get("beaker.session.id")
        return self.account_info is not None

    def _fetch_parsed_channels(self, skip_drm: bool = False) -> bool:
        self.http.headers.update(self._base_headers())
        url = self.channels_url
        if self.power_guide_hash:
            url = f"https://zattoo.com/zapi/v4/cached/{self.power_guide_hash}/channels"
            #url = f"{self.channels_url}/{self.power_guide_hash}/channels"
        resp = self.http.get(url)
        if not resp.ok:
            fallback = self.http.get(self.channels_url)
            if not fallback.ok:
                self.print(f"Channel fetch failed: {resp.status_code}/{fallback.status_code}")
                return False
            data = fallback.json()
        else:
            data = resp.json()
        self.channels = data.get("channels", [])
        return len(self.channels) > 0

    def _find_channel(self, channel_id: str) -> dict | None:
        for ch in self.channels:
            if ch.get("cid") == channel_id or ch.get("url_cid") == channel_id:
                return ch
            if channel_id in ch.get("alias_cids", []):
                return ch
        return None

    def _best_quality(self, ch: dict) -> dict | None:
        qualities = ch.get("qualities", [])
        if not qualities:
            return None
        availability_rank = {
            "available": 3,
            "subscribable": 2,
            "pvr_only": 1,
        }
        level_rank = {
            "hd": 2,
            "sd": 1,
        }
        scored = sorted(
            qualities,
            key=lambda q: (
                availability_rank.get(q.get("availability", ""), 0),
                0 if q.get("drm_required") is True else 1,
                level_rank.get(q.get("level", ""), 0),
            ),
            reverse=True,
        )
        best = scored[0]
        if availability_rank.get(best.get("availability", ""), 0) == 0:
            return None
        return best

    def _is_channel_playable(self, ch: dict) -> bool:
        best = self._best_quality(ch)
        if not best:
            return False
        return best.get("availability") == "available"

    """
    def _channel_cids(self, ch: dict, requested_channel_id: str | None = None) -> list[str]:
        ids = []
        if requested_channel_id:
            ids.append(requested_channel_id)
        for key in ("cid", "url_cid"):
            value = ch.get(key)
            if value and value not in ids:
                ids.append(value)
        for alias in ch.get("alias_cids", []):
            if alias and alias not in ids:
                ids.append(alias)
        return ids
    """

    def _channel_cids(self, ch: dict, requested_channel_id: str | None = None) -> list[str]:
        ids = []
        if requested_channel_id:
            ids.append(requested_channel_id)
        for key in ("cid", "url_cid"):
            value = ch.get(key)
            if value and value not in ids:
                ids.append(value)
        for alias in ch.get("alias_cids", []):
            if alias and alias not in ids:
                ids.append(alias)
        return ids

    def _pick_logo(self, ch: dict) -> str:
        best = self._best_quality(ch)
        if best and best.get("logo_token"):
            return f"{self.logo_base_url}/{best.get('logo_token')}/black/210x120.png"
        for q in ch.get("qualities", []):
            token = q.get("logo_token")
            if token:
                return f"{self.logo_base_url}/{token}/black/210x120.png"
        return ""

    def _channel_title(self, ch: dict) -> str:
        best = self._best_quality(ch)
        if best and best.get("title"):
            return best.get("title")
        return ch.get("title") or ch.get("cid") or "unknown"

    def _resolve_live_watch_url(self, ch: dict, requested_channel_id: str | None = None) -> str | None:
        self.http.headers.update(self._base_headers())
        stream_types = ["hls7", "hls5", "hls", "hls6"]
        cids = self._channel_cids(ch, requested_channel_id=requested_channel_id)
        self.print(f"Live watch resolve start: requested={requested_channel_id} cids={cids}")
        for cid in cids:
            endpoint = f"{self.watch_live_base_url}/{cid}"
            for stream_type in stream_types:
                payload = {
                    "stream_type": stream_type,
                    "https_watch_urls": True,
                    "timeshift": 10800,
                    "uuid": self.uuid,
                    "client_app_token": self.client_app_token or "",
                }
                self.print(f"Live watch try: cid={cid} stream_type={stream_type}")
                try:
                    resp = self.http.post(endpoint, data=payload, timeout=15)
                except Exception as ex:
                    self.print(f"Live watch request error for '{cid}' ({stream_type}): {ex}")
                    continue
                self.print(f"Live watch response: cid={cid} stream_type={stream_type} status={resp.status_code}")
                if not resp.ok:
                    self.log(f"Live watch non-ok body ({cid}/{stream_type}): {resp.text[:300]}")
                    continue
                try:
                    data = resp.json()
                except Exception as ex:
                    self.print(f"Live watch response parse error for '{cid}' ({stream_type}): {ex}")
                    self.log(f"Live watch raw response ({cid}/{stream_type}): {resp.text[:300]}\n\n")
                    continue
                stream = data.get("stream", {})
                watch_urls = stream.get("watch_urls", [])
                self.print(
                    f"Live watch parsed: cid={cid} stream_type={stream_type} "
                    f"keys={list(data.keys())[:8]} stream_keys={list(stream.keys())[:8]} watch_urls_type={type(watch_urls).__name__}"
                )
                if isinstance(watch_urls, list):
                    for entry in watch_urls:
                        if isinstance(entry, dict):
                            url = entry.get("url")
                            if url:
                                self.print(f"Live watch URL found in watch_urls for cid={cid} stream_type={stream_type}\n\n")
                                return url
                url = stream.get("url")
                if url:
                    self.print(f"Live watch URL found in stream.url for cid={cid} stream_type={stream_type}\n\n")
                    return url
                internal_code = data.get("internal_code")
                if internal_code is not None:
                    self.print(f"Live watch no-url internal_code for cid={cid} stream_type={stream_type}: {internal_code}")
        self.print(f"Live watch call failed for channel '{ch.get('cid', 'unknown')}'\n\n")
        return None

    """
    def _resolve_live_dash_widevine(self, ch: dict, requested_channel_id: str | None = None) -> dict | None:
        self.http.headers.update(self._base_headers())
        for cid in self._channel_cids(ch, requested_channel_id=requested_channel_id):
            endpoint = f"{self.watch_live_base_url}/{cid}"
            payload = {
                "stream_type": "dash_widevine",
                "https_watch_urls": True,
                "timeshift": 10800,
                "uuid": self.uuid,
                "client_app_token": self.client_app_token or "",
            }
            try:
                resp = self.http.post(endpoint, data=payload, timeout=15)
                if not resp.ok:
                    continue
                data = resp.json()
            except Exception:
                continue
            stream = data.get("stream", {})
            watch_urls = stream.get("watch_urls") or []
            entry = watch_urls[0] if isinstance(watch_urls, list) and watch_urls else {}
            mpd_url = entry.get("url") or stream.get("url")
            license_url = entry.get("license_url") or stream.get("license_url")
            if mpd_url and license_url:
                return {"mpd_url": mpd_url, "license_url": license_url, "cid": cid}
        return None
    """

    def _resolve_live_dash_widevine(
        self,
        ch: dict,
        requested_channel_id: str | None = None,
        timeout_seconds: float = 2.5,
    ) -> dict | None:
        self.http.headers.update(self._base_headers())
        cids = self._channel_cids(ch, requested_channel_id=requested_channel_id)
        self.log(f"DASH resolve start: requested={requested_channel_id} cids={cids} timeout={timeout_seconds}")
        for cid in cids:
            endpoint = f"{self.watch_live_base_url}/{cid}"
            payload = {
                "stream_type": "dash_widevine",
                "https_watch_urls": True,
                "timeshift": 10800,
                "uuid": self.uuid,
                "client_app_token": self.client_app_token or "",
            }
            self.log(f"DASH resolve try: cid={cid} endpoint={endpoint}")
            try:
                resp = self.http.post(endpoint, data=payload, timeout=timeout_seconds)
                if not resp.ok:
                    self.log(f"DASH resolve non-ok: cid={cid} status={resp.status_code} body={resp.text[:300]}")
                    continue
                data = resp.json()
            except Exception as ex:
                self.log(f"DASH resolve request/parse error: cid={cid} error={ex}")
                continue
            stream = data.get("stream", {})
            watch_urls = stream.get("watch_urls") or []
            entry = watch_urls[0] if isinstance(watch_urls, list) and watch_urls else {}
            mpd_url = entry.get("url") or stream.get("url")
            license_url = entry.get("license_url") or stream.get("license_url")
            self.log(
                f"DASH resolve parsed: cid={cid} internal_code={data.get('internal_code')} "
                f"keys={list(data.keys())[:8]} stream_keys={list(stream.keys())[:8]} "
                f"watch_urls_len={len(watch_urls) if isinstance(watch_urls, list) else -1} "
                f"has_mpd={bool(mpd_url)} has_license={bool(license_url)}"
            )
            if mpd_url and license_url:
                self.log(f"DASH resolve success: cid={cid} mpd={mpd_url[:160]} license={license_url[:160]}")
                try:
                    os.makedirs("cache/zattootv", exist_ok=True)
                    ts = datetime.now(timezone.utc).strftime("%Y%m%d_%H%M%S")
                    raw_debug_path = f"cache/zattootv/dash_watch_live_raw_{cid}_{ts}.json"
                    with open(raw_debug_path, "w", encoding="utf-8") as f:
                        f.write(resp.text)
                    self.log(f"DASH watch/live raw saved: cid={cid} path={raw_debug_path} chars={len(resp.text)}")
                except Exception as ex:
                    self.log(f"DASH watch/live raw save error: cid={cid} error={ex}")
                return {"mpd_url": mpd_url, "license_url": license_url, "cid": cid}
        self.log(f"DASH resolve failed: requested={requested_channel_id} cids={cids}")
        return None


    def _xmltv_time(self, unix_ts: int) -> str:
        return datetime.fromtimestamp(unix_ts, tz=timezone.utc).strftime("%Y%m%d%H%M%S +0000")

    def _build_epg_xml(self, start_ts: int, end_ts: int) -> str:
        if not self.lineup_hash:
            return "<?xml version=\"1.0\" encoding=\"UTF-8\"?><tv></tv>"
        self.http.headers.update(self._base_headers())
        url = f"https://zattoo.com/zapi/v3/cached/{self.lineup_hash}/guide?start={start_ts}&end={end_ts}"
        resp = self.http.get(url)
        if not resp.ok:
            self.print(f"EPG fetch failed: {resp.status_code}\nURL: {url}\nResponse: {resp.text}")
            return "<?xml version=\"1.0\" encoding=\"UTF-8\"?><tv></tv>"
        data = resp.json()
        epg_channels = data.get("channels", {})
        lines = [
            "<?xml version=\"1.0\" encoding=\"UTF-8\"?>",
            f"<tv generator-info-name=\"{_xe(self.provider_name)}\">",
        ]
        for ch in self.channels:
            cid = ch.get("cid", "")
            if not cid:
                self.print(f"Channel without CID skipped in EPG: {ch.get('title', 'unknown')}")
                continue
            lines.append(f"  <channel id=\"{_xe(cid, quote=True)}\">")
            lines.append(f"    <display-name>{_xe(ch.get('title', cid))}</display-name>")
            logo = self._pick_logo(ch)
            if logo:
                lines.append(f"    <icon src=\"{_xe(logo, quote=True)}\"/>")
            lines.append("  </channel>")
        for cid, entries in epg_channels.items():
            for item in entries:
                s = item.get("s")
                e = item.get("e")
                if not s or not e:
                    continue
                lines.append(f"  <programme start=\"{self._xmltv_time(s)}\" stop=\"{self._xmltv_time(e)}\" channel=\"{_xe(cid, quote=True)}\">")
                lines.append(f"    <title>{_xe(item.get('t', ''))}</title>")
                if item.get("et"):
                    lines.append(f"    <desc>{_xe(item.get('et', ''))}</desc>")
                if item.get("g"):
                    lines.append(f"    <category>{_xe(', '.join(item.get('g', [])))}</category>")
                if item.get("i_url"):
                    lines.append(f"    <icon src=\"{_xe(item.get('i_url'), quote=True)}\"/>")
                lines.append("  </programme>")
        lines.append("</tv>")
        return "\n".join(lines)

    def _login(self, username: str, password: str) -> bool:
        if not username or not password:
            return False
        if not self.client_app_token and not self._fetch_token():
            return False
        self.http.headers.update(self._base_headers())
        resp = self.http.post(
            self.login_url,
            data={
                "login": username,
                "password": password,
                "remember": "True",
                "keep_lang": "False",
            },
        )
        try:
            data = resp.json()
        except Exception:
            data = {}
        internal_code = data.get("internal_code")
        if resp.ok and (data.get("active") is True or data.get("account") is not None):
            self._raw_hello_response = data
            self.account_info = data.get("account")
            self.public_id = (self.account_info or {}).get("public_id")
            self.zpush_url = (self.account_info or {}).get("zpush_url")
            self.power_guide_hash = data.get("power_guide_hash")
            self.lineup_hash = data.get("lineup_hash")
            assets = data.get("assets", {})
            self.image_base_url = assets.get("image_base_url", self.image_base_url)
            self.logo_base_url = assets.get("logo_base_url", self.logo_base_url)
            self.beaker_session_id = self.http.cookies.get("beaker.session.id")
            return True
        if internal_code == 200 or self.http.cookies.get("beaker.session.id"):
            return self._fetch_hello()
        if not resp.ok:
            self.print(f"Login failed: {resp.status_code} {data}")
            return False
        return self._fetch_hello()    

    def _ensure_valid(self):
        """
        {
        "permissions": [
            "SRDE",
            "base_ultimate",
            "ultimate"
        ],
        "name": "your@account.de",
        "public_id": "7-------------------37c",
        "zpush_url": "https://zpush.zattoo.com/sse?public_id=xxx&client_id=xxx&mac=xxx",
        "service_country": "DE",
        "privacy_settings": []
        }        
        """
        if self.account_info is None:
            print("No account info available, trying to boot ...")
            self.boot()
        
        if self.account_info and len(self.account_info["permissions"]) > 0:
            return True
        return False

    # --- 
    def boot(self, login_flow: bool = False):
        if not self._load_cookies() and not login_flow:
            self.print("No valid cookies found, run zattoo_login.py first.")
            return False
        if not self._fetch_hello():
            self.print("Hello failed, cookies likely expired.")
            return False
        if not self._fetch_parsed_channels():
            self.print("Channel fetch failed.")
            return False
        return True

    def _live_stream_ffmpeg(self, channel_id, timeout: int = 30, selfproxy: bool = False):
        return self._live_stream_hls(channel_id)

    def _live_stream_hls(self, channel_id):
        if not self.channels and not self._ensure_valid():
            return Response("Zattoo session invalid", status=503)
        ch = self._find_channel(channel_id)
        if not ch:
            return Response("Channel not found", status=404)
        if not self._is_channel_playable(ch):
            return Response("Channel currently not available", status=403)
        try:
            watch_url = self._resolve_live_watch_url(ch, requested_channel_id=channel_id)
        except Exception as ex:
            self.print(f"Live stream resolve error for '{channel_id}': {ex}")
            return Response("No live stream URL available", status=502)
        if not watch_url:
            return Response("No live stream URL available", status=502)
        return redirect(watch_url, code=302)

    def playlist_m3u(self) -> str:
        return self._playlist_cache.get("m3u", self._playlist_m3u)
    
    def playlist_m3u_kodi(self) -> str:
        return self._playlist_cache_kodi.get("m3u_kodi", self._playlist_m3u_kodi)

    def _playlist_m3u(self) -> str:
        if not self.channels and not self._ensure_valid():
            return Response("Zattoo session invalid", status=503)
        proxy = self.get_proxy_base_url()
        lines = ["#EXTM3U"]
        for ch in sorted(self.channels, key=lambda x: x.get("number", 99999)):
            if ch.get("is_radio"):
                continue
            if not self._is_channel_playable(ch):
                continue
            cid = ch.get("cid")
            if not cid:
                continue
            name = self._channel_title(ch)
            logo = self._pick_logo(ch)
            lines.append(f"#EXTINF:-1 tvg-id=\"{_xe(cid, quote=True)}\" tvg-name=\"{_xe(name, quote=True)}\" tvg-logo=\"{_xe(logo, quote=True)}\" group-title=\"{self.provider_name}\",{_xe(name)}")
            ext = "ts" if self.ffmpeg else "m3u8"
            lines.append(f"{proxy}/live/{cid}.{ext}")
        return Response("\n".join(lines), mimetype="audio/x-mpegurl")

    """
    def playlist_m3u_kodi(self) -> str:
        if not self.channels and not self.boot():
            return Response("Zattoo session invalid", status=503)

        proxy = self.get_proxy_base_url()
        lines = ["#EXTM3U"]

        for ch in sorted(self.channels, key=lambda x: x.get("number", 99999)):
            if ch.get("is_radio"):
                continue
            if not self._is_channel_playable(ch):
                continue

            cid = ch.get("cid")
            if not cid:
                continue

            name = self._channel_title(ch)
            logo = self._pick_logo(ch)
            lines.append(
                f"#EXTINF:-1 tvg-id=\"{_xe(cid, quote=True)}\" tvg-name=\"{_xe(name, quote=True)}\" tvg-logo=\"{_xe(logo, quote=True)}\" group-title=\"ZattooTV\",{_xe(name)}"
            )

            dash = self._resolve_live_dash_widevine(ch, requested_channel_id=cid)
            if dash and dash.get("mpd_url") and dash.get("license_url"):
                license_key = (
                    f"{dash['license_url']}|"
                    f"User-Agent={ZATTOOTV_USERAGENT}&Origin=https://zattoo.com&Referer=https://zattoo.com/|"
                    f"R{{SSM}}|"
                )
                lines.append("#KODIPROP:inputstream=inputstream.adaptive")
                lines.append("#KODIPROP:inputstream.adaptive.manifest_type=mpd")
                lines.append("#KODIPROP:inputstream.adaptive.license_type=com.widevine.alpha")
                lines.append(f"#KODIPROP:inputstream.adaptive.license_key={license_key}")
                lines.append(dash["mpd_url"])

            lines.append("#KODIPROP:inputstream=inputstream.adaptive")
            lines.append("#KODIPROP:inputstream.adaptive.manifest_type=hls")
            lines.append(f"{proxy}/live/{cid}.m3u8")

        return Response("\n".join(lines), mimetype="audio/x-mpegurl")
    """

    def _playlist_m3u_kodi(self) -> str:
        if not self.channels and not self._ensure_valid():
            return Response("Zattoo session invalid", status=503)

        proxy = self.get_proxy_base_url()
        lines = ["#EXTM3U"]

        for ch in sorted(self.channels, key=lambda x: x.get("number", 99999)):
            if ch.get("is_radio"):
                continue
            if not self._is_channel_playable(ch):
                continue

            cid = ch.get("cid")
            if not cid:
                continue

            name = self._channel_title(ch)
            logo = self._pick_logo(ch)
            lines.append(
                f"#EXTINF:-1 tvg-id=\"{_xe(cid, quote=True)}\" tvg-name=\"{_xe(name, quote=True)}\" tvg-logo=\"{_xe(logo, quote=True)}\" group-title=\"ZattooTV\",{_xe(name)}"
            )

            qualities = ch.get("qualities") or []
            if isinstance(qualities, dict):
                qualities = list(qualities.values())
            elif not isinstance(qualities, list):
                qualities = list(qualities)
            try_dash = any(
                isinstance(q, dict)
                and bool(q.get("drm_required"))
                and str(q.get("availability", "")).lower() in ("available", "subscribable")
                for q in qualities
            )
            self.log(
                f"Kodi playlist channel: cid={cid} name={name} "
                f"try_dash={try_dash} qualities_count={len(qualities)}"
            )

            if try_dash:
                dash = self._resolve_live_dash_widevine(ch, requested_channel_id=cid, timeout_seconds=2.5)
            else:
                dash = None

            if dash and dash.get("mpd_url") and dash.get("license_url"):
                self.log(f"Kodi playlist DASH selected: cid={cid} dash_cid={dash.get('cid')}")
                license_key = (
                    f"{dash['license_url']}|"
                    f"User-Agent={ZATTOOTV_USERAGENT}&Origin=https://zattoo.com&Referer=https://zattoo.com/|"
                    f"R{{SSM}}|"
                )
                lines.append("#KODIPROP:inputstream=inputstream.adaptive")
                lines.append("#KODIPROP:inputstream.adaptive.manifest_type=mpd")
                lines.append("#KODIPROP:inputstream.adaptive.license_type=com.widevine.alpha")
                lines.append(f"#KODIPROP:inputstream.adaptive.license_key={license_key}")
                lines.append(dash["mpd_url"])
            elif try_dash:
                self.log(f"Kodi playlist DASH required but unresolved: cid={cid}; channel skipped")
                continue
            else:
                self.log(f"Kodi playlist HLS fallback selected: cid={cid}")
                lines.append("#KODIPROP:inputstream=inputstream.adaptive")
                lines.append("#KODIPROP:inputstream.adaptive.manifest_type=hls")
                lines.append(f"{proxy}/live/{cid}.m3u8")
        #return Response("\n".join(lines), mimetype="audio/x-mpegurl")
        return "\n".join(lines)
    
    def get_epg_xml(self) -> str:
        """Öffentliche Methode, jetzt mit TTL-Cache."""
        return self._epg_cache.get("epg", self._get_epg_xml)

    def _get_epg_xml(self) -> str:
        if not self.channels and not self._ensure_valid():
            return Response("Zattoo session invalid", status=503)
        now = datetime.now(timezone.utc)
        start = int((now - timedelta(hours=-1)).timestamp())
        end = int((now + timedelta(hours=ZATTOOTV_EPG_DURATION_HOURS)).timestamp())
        #return Response(self._build_epg_xml(start, end), mimetype="application/xml")
        return self._build_epg_xml(start, end)
    
    def get_channels(self):
        if not self.channels: # and not self.boot():
            return []
        return self.channels

    def live_stream(self, channel_id: str):
        if self.ffmpeg:
            return self._live_stream_ffmpeg(channel_id)
        return self._live_stream_hls(channel_id)
