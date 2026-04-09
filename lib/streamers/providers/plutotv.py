from ..streamerbase import StreamerBase
from datetime import datetime, timezone, timedelta
import urllib.parse
import json, base64
import time
import threading
import uuid
from flask import Response
import re
from html import escape as _xe
from lib.utils.ttlcache import TTLCache
from lib.utils.ffmpegwrapper import FFmpegWrapper

PLUTO_USERAGENT = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/145.0.0.0 Safari/537.36"
PLUTO_EPG_DURATION_MIN  = 720   # Minuten EPG-Dauer pro Request (kann je nach Bedarf angepasst werden)
PLUTO_EPG_BATCH_SIZE    = 100   # Kanal-IDs pro API-Request (URL-Länge begrenzen)

PLUTO_BOOT_URL = (
    "https://boot.pluto.tv/v4/start"
    "?appName=web"
    "&appVersion=9.19.0-7a6c115631d945c4f7327de3e03b7c474b692657"
    "&deviceVersion=145.0.0"
    "&deviceModel=web"
    "&deviceMake=chrome"
    "&deviceType=web"
    "&clientID={cid}"
    "&clientModelNumber=1.0.0"
    "&channelID="
    "&serverSideAds=false"
    "&drmCapabilities=widevine%3AL3"
    "&blockingMode="
    "&notificationVersion=1"
    "&appLaunchCount=0"
    "&lastAppLaunchDate={dt}"
    "&clientTime={dt}"
)

class PlutoTV(StreamerBase):
    def __init__(self, debug: bool = False, ip: str = "localhost", port: int = 7080, **kwargs):
        # required init of base class (StreamerBase) to set common attributes and http session
        super().__init__(debug=debug, ip=ip, port=port, **kwargs)

        # set the provider name (used in playlist URLs and logs, needs to be unique, may change later - WIP Class / Filename is unique )
        self.provider_name = "PlutoTV"

        # threading lock for JWT refresh (double-checked locking pattern)
        self._lock = threading.Lock()

        # header for plutotv
        self.http.headers.update({
            "User-Agent": PLUTO_USERAGENT})
        
        # data from boot response
        self.jwt_token = None
        self.jwt_exp = 0
        
        self.stitcher_params = None
        self.stitcher_base = None
        self.session = None

        self.channels = None
        self.servers = None
        self.categories = None
        self.categories_mapped = None

        # EPG
        # Cache: 6 Stunden (360 Minuten)
        self._epg_cache = TTLCache[str](ttl_minutes=360)

        # CHANNELS
        # Cache: 120 Minuten
        self._channels_cache = TTLCache[str](ttl_minutes=120)


    # -------------------------------------------------
    # helper functions

    def _load_channels(self):
        self._ensure_valid()

        """Lädt alle verfügbaren Kanäle vom Channels-API-Server."""
        if not self.servers["channels"]:
            self.print("Kein Channels-Server gefunden.")
            return
        
        url = (
            self.servers["channels"]
            + "/v2/guide/channels?channelIds=&offset=0&limit=1000&sort=number%3Aasc"
        )
        self.http.headers.update({"Authorization": f"Bearer {self.jwt_token}"})
        resp = self.http.get(url)

        if resp.ok:
            self.channels = resp.json().get("data", [])
            self.print(f"{len(self.channels)} Kanäle geladen.")
            return self.channels
        else:
            self.print(f"Kanäle konnten nicht geladen werden: {resp.status_code}")
            self.log(resp.text)

    def _ensure_valid(self):
        """
        Prüft ob das JWT noch mindestens 5 Minuten gültig ist.
        Falls nicht, wird boot() erneut aufgerufen.
        Double-Checked Locking: verhindert doppelten Refresh bei parallelen Requests.
        """
        if time.time() > self.jwt_exp - 300:
            with self._lock:
                if time.time() > self.jwt_exp - 300:
                    self.print("JWT läuft ab - erneuere Session ...")
                    self.boot()

    def _parse_jwt_exp(self):
        """Liest den exp-Wert (Ablaufzeit) aus dem JWT-Payload (Base64-Decode)."""
        try:
            payload_b64 = self.jwt_token.split(".")[1]
            payload_b64 += "=" * (-len(payload_b64) % 4)   # Padding ergänzen
            payload = json.loads(base64.b64decode(payload_b64))
            self.jwt_exp = payload.get("exp", 0)
            self.print(f"JWT-Token läuft ab am {datetime.fromtimestamp(self.jwt_exp, tz=timezone.utc).isoformat()} (UTC)")
        except Exception as e:
            self.print(f"JWT-Parsing fehlgeschlagen: {e}")
            self.jwt_exp = 0

    def _get_logo(self, ch: dict) -> str:
        """Extrahiert die Logo-URL aus einem Channel-Objekt (robust für verschiedene Formate)."""
        images = ch.get("images", {})
        if isinstance(images, dict):
            for key in ("logo", "thumbnail", "featuredImage", "poster"):
                img = images.get(key)
                if isinstance(img, dict):
                    return img.get("path") or img.get("url") or ""
                if isinstance(img, str) and img:
                    return img
        elif isinstance(images, list):
            for img in images:
                if isinstance(img, dict) and img.get("type") in ("logo", "thumbnail"):
                    return img.get("url", "")
        return ""
    
    def _master_url(self, channel_id: str) -> str:
        """Baut die vollständige Master-Playlist-URL für einen Kanal."""
        return (
            f"{self.stitcher_base}/v2/stitch/hls/channel/{channel_id}/master.m3u8"
            f"?{self.stitcher_params}&jwt={self.jwt_token}"
        )

    def _variant_url(self, channel_id: str, relative_uri: str) -> str:
        """
        Baut die vollständige URL für eine Varianten-Playlist.
        relative_uri enthält bereits sid und deviceId (vom Stitcher eingebettet).
        Wir ergänzen nur das JWT.
        """
        base = f"{self.stitcher_base}/v2/stitch/hls/channel/{channel_id}/"
        if "?" in relative_uri:
            path, qs = relative_uri.split("?", 1)
            return f"{base}{path}?{qs}&jwt={self.jwt_token}"
        # Fallback: stitcherParams verwenden wenn kein Query-String vorhanden
        return f"{base}{relative_uri}?{self.stitcher_params}&jwt={self.jwt_token}"

    def _parse_best_variant(self, master_content: str) -> dict | None:
        """
        Parst eine HLS Master-Playlist und gibt die Variante mit der
        höchsten Bandbreite zurück.

        Rückgabe: {'bandwidth': int, 'uri': str} oder None wenn keine Variante gefunden.
        """
        best = None
        lines = master_content.splitlines()
        for i, line in enumerate(lines):
            if not line.startswith("#EXT-X-STREAM-INF:"):
                continue
            bw_match  = re.search(r"BANDWIDTH=(\d+)", line)
            bandwidth = int(bw_match.group(1)) if bw_match else 0

            # Nächste nicht-leere Nicht-Kommentar-Zeile ist die Varianten-URI
            for uri_line in lines[i + 1:]:
                uri_stripped = uri_line.strip()
                if uri_stripped and not uri_stripped.startswith("#"):
                    if best is None or bandwidth > best["bandwidth"]:
                        best = {"bandwidth": bandwidth, "uri": uri_stripped}
                    break

        return best
    
    def _make_segments_absolute(self, playlist_content: str, playlist_url: str) -> str:
        """
        Wandelt relative Segment-URLs in einer Varianten-Playlist in absolute URLs um.
        Der Player kann Segmente dann direkt vom PlutoTV-CDN laden (kein JWT nötig).

        playlist_url: die URL von der die Playlist abgerufen wurde (für relative Auflösung).

        Besonderheit: #EXT-X-ENDLIST wird herausgefiltert.
        Dieser Tag signalisiert dem Player das Stream-Ende (VOD-Verhalten) und tritt
        bei PlutoTV an Sendungsgrenzen auf, was den Stream in Kodi stoppt.
        """
        # Basis: Verzeichnis der Playlist-URL (ohne Query-String)
        base = playlist_url.split("?")[0].rsplit("/", 1)[0] + "/"

        result = []
        for line in playlist_content.splitlines():
            clean = line.strip()

            # #EXT-X-ENDLIST: PlutoTV sends this at show boundaries, causing players to stop
            if clean == "#EXT-X-ENDLIST":
                self.print("[HLS] #EXT-X-ENDLIST filtered")
                continue

            # Segment URLs: make absolute
            if clean and not clean.startswith("#"):
                if not clean.startswith("http"):
                    clean = urllib.parse.urljoin(base, clean)
                result.append(clean)
            else:
                result.append(line.rstrip("\r"))

        output = "\n".join(result)
        self.log(f"[HLS] Playlist ({len(result)} lines):\n{output}")
        return output

    def _fetch_epg_batch(self, channel_ids: list[str]) -> dict:
        """Holt EPG-Timelines für einen Batch von Kanal-IDs vom PlutoTV-API."""
        last_hour_iso = self._iso_time(encode=False)
        url = (
            self.servers["channels"]
            + "/v2/guide/timelines"
            + f"?start={last_hour_iso}"
            + f"&channelIds={','.join(channel_ids)}"
            + f"&duration={PLUTO_EPG_DURATION_MIN}"
        )
        resp = self.http.get(url, headers={"Authorization": f"Bearer {self.jwt_token}"})
        if not resp.ok:
            self.print(f"[EPG] Fehler {resp.status_code} für Batch ({len(channel_ids)} Kanäle)")
            return []
        return resp.json().get("data", [])

    def _build_epg_xml(self) -> str:
        """
        Holt EPG-Daten für alle Kanäle in Batches und gibt einen XMLTV-String zurück.
        Zeitfenster: 2h zurück bis +10h (via _iso_time Default + PLUTO_EPG_DURATION_MIN).
        Wird über get_epg_xml() gecached mittels TTLCache
        """
        self._ensure_valid()
        all_ids = [ch.get("id", "") for ch in self.channels if ch.get("id")]

        # EPG in Batches abrufen, positional zu channel_ids zuordnen
        timelines_by_channel: dict[str, list] = {}
        for i in range(0, len(all_ids), PLUTO_EPG_BATCH_SIZE):
            batch_ids  = all_ids[i : i + PLUTO_EPG_BATCH_SIZE]
            batch_data = self._fetch_epg_batch(batch_ids)
            for j, item in enumerate(batch_data):
                ch_id = item.get("channelId") or (batch_ids[j] if j < len(batch_ids) else None)
                if ch_id:
                    timelines_by_channel[ch_id] = item.get("timelines", [])
        lines = [
            '<?xml version="1.0" encoding="UTF-8"?>',
            f'<tv generator-info-name="{self.provider_name}">',
        ]
        
        # <channel>-Einträge
        for ch in self.channels:
            ch_id = ch.get("id", "")
            name  = _xe(ch.get("name", ch_id))
            logo  = self._get_logo(ch)
            lines.append(f'  <channel id="{_xe(ch_id, quote=True)}">')
            lines.append(f'    <display-name>{name}</display-name>')
            if logo:
                lines.append(f'    <icon src="{_xe(logo, quote=True)}"/>')
            lines.append('  </channel>')

        # <programme>-Einträge
        for ch in self.channels:
            ch_id = ch.get("id", "")
            for tl in timelines_by_channel.get(ch_id, []):
                start = self._xmltv_time(tl.get("start", ""))
                stop  = self._xmltv_time(tl.get("stop",  ""))
                title = _xe(tl.get("title", ""))

                ep      = tl.get("episode", {})
                desc    = _xe(ep.get("description", ""))
                genre   = _xe(ep.get("genre", ""))
                ep_name = _xe(ep.get("name", ""))
                season  = ep.get("season",  0)
                ep_num  = ep.get("number",  0)
                rating  = _xe(ep.get("rating", ""))
                thumb   = ep.get("thumbnail", {}).get("path", "")
                series  = ep.get("series", {}).get("name", "")

                lines.append(f'  <programme start="{start}" stop="{stop}" channel="{_xe(ch_id, quote=True)}">')
                lines.append(f'    <title>{title}</title>')
                if ep_name:
                    lines.append(f'    <sub-title>{ep_name}</sub-title>')
                if desc:
                    lines.append(f'    <desc>{desc}</desc>')
                if series and ep_num:
                    s = max(0, season - 1)
                    e = max(0, ep_num  - 1)
                    lines.append(f'    <episode-num system="xmltv_ns">{s}.{e}.0/1</episode-num>')
                if genre:
                    lines.append(f'    <category>{genre}</category>')
                if thumb:
                    lines.append(f'    <icon src="{_xe(thumb, quote=True)}"/>')
                if rating:
                    lines.append(f'    <rating><value>{rating}</value></rating>')
                lines.append('  </programme>')

        lines.append('</tv>')
        return '\n'.join(lines)

    def _iso_time(self, iso_time: str = None, encode: bool = False) -> str:
        """Gibt PlutoTV-ISO-Format MIT IMMER .000 Millisekunden: bei iso_time parsen, sonst UTC minus 1 Stunde."""
        if iso_time is None:
            # Aktuelle UTC minus 1 Stunde, Sekunden/Ms auf 0
            dt = datetime.now(timezone.utc) - timedelta(hours=1)
            dt = dt.replace(second=0, microsecond=0)
        else:
            # Parse und explizit Sekunden/Ms auf 0
            dt_naive = datetime.strptime(iso_time, "%Y-%m-%d %H:%M:%S").replace(microsecond=0)
            dt = dt_naive.replace(tzinfo=timezone.utc)
        
        iso_str = dt.isoformat(timespec="milliseconds").replace("+00:00", "Z")
        
        if encode:
            return urllib.parse.quote(iso_str, safe="")
        return iso_str

    def _xmltv_time(self, iso_str: str) -> str:
        """
        Konvertiert ISO 8601 UTC-String ins XMLTV-Zeitformat: YYYYMMDDHHmmss +0000.
        Python 3.10-kompatibel: strptime statt fromisoformat (3-stellige ms-Problem).
        """
        for fmt in ("%Y-%m-%dT%H:%M:%S.%fZ", "%Y-%m-%dT%H:%M:%SZ"):
            try:
                return datetime.strptime(iso_str, fmt).strftime("%Y%m%d%H%M%S +0000")
            except ValueError:
                continue
        # Fallback für Python 3.11+
        return datetime.fromisoformat(iso_str.replace("Z", "+00:00")).strftime("%Y%m%d%H%M%S +0000")

    def _live_stream_hls(self, channel_id: str):
        self._ensure_valid()

        """Standard HLS passthrough: rewrites playlist URLs, player handles segments directly."""
        master_url = self._master_url(channel_id)
        self.log(f"[HLS] Fetching master playlist for channel:{channel_id}")
        resp = self.http.get(master_url)
        if not resp.ok:
            self.print(f"[HLS] Master error {resp.status_code} for {channel_id}")
            return None

        best = self._parse_best_variant(resp.text)
        if not best:
            self.print(f"[HLS] No variants found for {channel_id}")
            return None

        v_url  = self._variant_url(channel_id, best["uri"])
        v_resp = self.http.get(v_url)
        if not v_resp.ok:
            self.print(f"[HLS] Variant error {v_resp.status_code} ({best['bandwidth']} bps)")
            return None

        # Fallback: some channels need the 'livestitch' endpoint
        has_segments = any(
            l.strip() and not l.strip().startswith("#")
            for l in v_resp.text.splitlines()
        )
        if not has_segments:
            self.print(f"[HLS] Empty playlist - trying livestitch fallback for '{channel_id}' ...")
            ls_url  = self._variant_url(channel_id + "livestitch", best["uri"])
            ls_resp = self.http.get(ls_url)
            if ls_resp.ok and any(
                l.strip() and not l.strip().startswith("#")
                for l in ls_resp.text.splitlines()
            ):
                self.print(f"[HLS] livestitch fallback successful for '{channel_id}'")
                v_url, v_resp = ls_url, ls_resp

        content = self._make_segments_absolute(v_resp.text, v_url)
        return Response(content, mimetype="application/vnd.apple.mpegurl")

    def _live_stream_ffmpeg(self, channel_id: str):
        """FFmpeg remux: handles HLS decryption + discontinuities, outputs clean MPEG-TS."""
        self._ensure_valid()

        #variant_url = self._resolve_variant_url(channel_id)
        variant_url = f"http://127.0.0.1:{self.port}/{self.provider_name.lower()}/live/{channel_id}.m3u8"

        if not variant_url:
            self.print("[FFmpeg] Can't find a variant. No stream for you today, buddy!")
            return None

        self.print(f"[FFmpeg] Starting stream for channel:{channel_id}")

        cmd = [
            self.ffmpeg_path, "-hide_banner", "-loglevel", "warning",
            "-reconnect", "1",
            "-reconnect_streamed", "1",
            "-reconnect_delay_max", "10",
            "-rw_timeout", "5000000",
            "-user_agent", self.USER_AGENT,
            "-headers", f"Authorization: Bearer {self.jwt_token}\r\n",
            "-i", variant_url,
            "-c", "copy",
            "-f", "mpegts",
            "pipe:1",
        ]

        ffmpeg = FFmpegWrapper(cmd=cmd, name=f"PlutoTV-{channel_id}", logger=self.print)
        ffmpeg.start()

        def generate():
            try:
                for chunk in ffmpeg.read_stdout(8192):
                    yield chunk # hier knallt es, wenn Kodi weg ist
            except OSError as e:
                self.print(f"[FFmpeg] Client disconnected for {channel_id}: {e}")
            finally:
                ffmpeg.stop()
        #return Response(ffmpeg.read_stdout(), mimetype="video/MP2T")
        return Response(generate(), mimetype="video/MP2T", direct_passthrough=True)


    def _resolve_variant_url(self, channel_id: str) -> str | None:
        """Resolves the best working variant URL for a channel (incl. livestitch fallback)."""
        master_url = self._master_url(channel_id)
        resp = self.http.get(master_url)
        if not resp.ok:
            self.print(f"[Resolve] Master error {resp.status_code} for {channel_id}")
            return None

        best = self._parse_best_variant(resp.text)
        if not best:
            self.print(f"[Resolve] No variants found for {channel_id}")
            return None

        v_url  = self._variant_url(channel_id, best["uri"])
        v_resp = self.http.get(v_url)
        if v_resp.ok and any(l.strip() and not l.strip().startswith("#") for l in v_resp.text.splitlines()):
            return v_url

        # Livestitch fallback
        ls_url  = self._variant_url(channel_id + "livestitch", best["uri"])
        ls_resp = self.http.get(ls_url)
        if ls_resp.ok and any(l.strip() and not l.strip().startswith("#") for l in ls_resp.text.splitlines()):
            self.print(f"[Resolve] Using livestitch fallback for '{channel_id}'")
            return ls_url

        self.print(f"[Resolve] No working variant found for {channel_id}")
        return None



    # -------------------------------------------------------------------------------------------------------------------------------------------------
    # abstract method implementations    

    def boot(self):
        # Aktuelles Datum und Uhrzeit im ISO-Format (UTC) für die Boot-URL einfügen
        now_utc = datetime.now(timezone.utc)
        launch_date = now_utc.isoformat(timespec='seconds').replace('+00:00', 'Z')

        url = PLUTO_BOOT_URL.format(dt=urllib.parse.quote(launch_date), cid=uuid.uuid4())
        response = self.http.get(url)
        if response.status_code == 200:
            data = response.json()
            self.jwt_token = data.get("sessionToken")
            self.servers = data.get("servers", {})
            self.stitcher_params = data.get("stitcherParams", {})
            self.stitcher_base   = self.servers.get("stitcher")
            self._parse_jwt_exp()

            self.print(f"Boot erfolgreich. Base URL: {self.get_proxy_base_url()}")
            self._load_channels()  # Kanäle direkt nach Boot laden (benötigt für Playlist und EPG)
        else:
            raise Exception(f"Boot fehlgeschlagen. Statuscode: {response.status_code}, Antwort: {response.text}")

    def live_stream(self, channel_id: str):
        self._ensure_valid()
        return self._live_stream_hls(channel_id)
                
    def vod_stream(self, vod_id: str): ...
    
    def playlist_m3u(self) -> str:
        """
        M3U-Playlist aller PlutoTV-Kanäle.
        Kompatibel mit VLC, Kodi IPTV Simple Client und Enigma2.
        Extension: .ts bei FFmpeg-Modus (MPEG-TS), sonst .m3u8 (HLS).
        """
        self._ensure_valid()
        extension = "ts" if self.ffmpeg else "m3u8"

        lines = ["#EXTM3U"]
        for ch in self.channels:
            ch_id  = ch.get("id", "")
            name   = ch.get("name", ch_id)
            number = ch.get("number", 0)
            logo   = self._get_logo(ch)

            category = self.provider_name

            lines.append(
                f'#EXTINF:-1 tvg-id="{ch_id}" tvg-name="{name}" '
                f'tvg-logo="{logo}" tvg-chno="{number}" '
                f'group-title="{category}",{name}'
            )
            base_url = self.get_proxy_base_url()
            lines.append(f"{base_url}/live/{ch_id}.{extension}")

        return Response("\n".join(lines))
    
    def get_epg_xml(self) -> str:
        """Öffentliche Methode, jetzt mit TTL-Cache."""
        return self._epg_cache.get("epg", self._build_epg_xml)
    
    def get_channels(self):
        """Öffentliche Methode, jetzt mit TTL-Cache."""
        return self._channels_cache.get("channels", self._load_channels)
    
    def get_vod_categories(self, limit: int = 1000):
        self._ensure_valid()
        if not self.servers["hub"]:
            self.print("Kein Hub-Server gefunden.")
            return
        url = self.servers.get("hub") + f"/v1/hub/home?limit={limit}"
        self.http.headers.update({"Authorization": f"Bearer {self.jwt_token}"})
        response = self.http.get(url)
        self.debug(f"Calling {url} for {self.provider_name}")
        if response.ok:
            categories_json = response.json()
            with open("plutotvclass_categories.json", "w", encoding="utf-8") as f:
                json.dump(categories_json, f, indent=4)
            print(json.dumps(categories_json, indent=4))
        pass

    def get_vod_category_list(self, category_id: str): str: ...