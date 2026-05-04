# wakdos-streamhub ![Version](https://img.shields.io/badge/version-v0.7.0-blue)

A modular Python proxy that unifies multiple streaming services (IPTV, VOD, EPG) behind a single local API.

## What it does

wakdos-streamhub acts as a central hub between streaming providers and your media player. Instead of configuring each service separately, you point your player (Kodi, VLC, Jellyfin, etc.) at one local endpoint and get M3U playlists, live streams, and EPG data for all configured providers.

## Features

- **Unified API** – One base URL, consistent endpoints across all providers
- **M3U Playlists** – Ready-to-use playlists compatible with IPTV Simple Client, VLC, and Enigma2
- **XMLTV EPG** – Electronic program guide in standard XMLTV format
- **Live Streaming** – HLS proxy with automatic quality selection
- **Ad Filler** – Optional `--ad-filler` replaces ad segments with a static filler image, preventing codec-switch crashes at ad breaks
- **FFmpeg Remux** – Optional MPEG-TS passthrough via FFmpeg (experimental, see known issues below)
- **Modular Providers** – Each streaming service is a self-contained plugin
- **Kodi Playlists** – Optional `--kodi` mode generates playlists with `inputstream.adaptive` props for native HLS handling in Kodi
- **Easy to Extend** – Drop in a new provider file and it's auto-discovered

## Supported Providers

| Provider | Status | DRM | Notes |
|----------|--------|-----|-------|
| PlutoTV  | ✅ Working | None | Free, anonymous, HLS |
| ??????   | 🔜 Planned | None | Login-based, FAST channels |
| ??????   | 🔜 Planned | Widevine L3 | First DRM test case |

## Quick Start

```bash
# Clone and set up
git clone https://github.com/wakdo/wakdos-streamhub.git
cd wakdos-streamhub
python -m venv venv
venv\Scripts\activate  # Windows
pip install -r requirements.txt

# ⭐ Recommended for KODi Users: Kodi with ad-filler (seamless playback, no ad crashes)
python app.py --ip 192.168.178.65 --port 7000 --kodi --ad-filler

# Standard HLS mode (VLC, Enigma2, etc.)
python app.py --ip 192.168.178.65 --port 7000 --ad-filler

# Without ad-filler (ads may cause stream crashes on some players)
python app.py --ip 192.168.178.65 --port 7000

# FFmpeg remux (experimental, see known issues)
python app.py --ip 192.168.178.65 --port 7000 --ffmpeg --ffmpeg-path /usr/bin/ffmpeg
```

Then point your player at:
- **Playlist:** `http://<ip>:7000/plutotv/playlist.m3u`
- **EPG:** `http://<ip>:7000/plutotv/epg.xml`

### CLI Options

| Option             | Default     | Description                                    |
|--------------------|-------------|------------------------------------------------|
| `--ip`             | `localhost` | IP for playlist URLs                           |
| `--flask-ip`       | `0.0.0.0`  | Flask bind address (listen on all interfaces)  |
| `--port`           | `7000`      | Port                                           |
| `--debug`          | `false`     | Enable debug logging                           |
| `--flaskdebug`     | `false`     | Enable Flask debug mode                        |
| `--ffmpeg`         | `false`     | FFmpeg remux for stutter-free streams          |
| `--ffmpeg-path`    | `ffmpeg`    | Path to FFmpeg binary                          |
| `--ffmpeg-timeout` | `30`        | Watchdog timeout (seconds) for idle FFmpeg processes |
| `--ffmpeg-selfproxy` | `false`   | Feed FFmpeg with proxy HLS playlist instead of raw provider URL |
| `--ad-filler`      | `false`     | Replace ad segments with a static filler image (prevents codec-switch crashes at ad breaks) |
| `--kodi`           | `false`     | Generate Kodi-compatible playlist with `inputstream.adaptive` props (HLS only, incompatible with `--ffmpeg`) |

### Environment Variables (PlutoTV)

| Variable | Default | Description |
|----------|---------|-------------|
| `PLUTO_USERAGENT` | Chrome 145 UA | User-Agent header for PlutoTV API requests |
| `PLUTO_EPG_DURATION_MIN` | `720` | EPG duration per request in minutes |
| `PLUTO_EPG_BATCH_SIZE` | `100` | Channel IDs per EPG API request (URL length limit) |
| `PLUTO_FILLER_MEDIA_PATH` | `filler_blackwhite_quiet.ts` | Filler segment filename in `lib/static/` (MPEG-TS, H.264 720p, AAC 48kHz) |
| `PLUTOTV_FFMPEG_DEBUGLEVEL` | `warning` | FFmpeg log level (`debug`, `info`, `warning`, `error`) |

## API Endpoints

All endpoints follow the pattern `/<provider>/...`:

| Endpoint                        | Description                                  |
|---------------------------------|----------------------------------------------|
| `GET /<provider>/playlist.m3u`  | M3U playlist (all channels)                  |
| `GET /<provider>/live/<id>`     | HLS live stream (or MPEG-TS with `--ffmpeg`) |
| `GET /<provider>/epg.xml`       | XMLTV EPG feed                               |

## Adding a Provider

1. Create a new file in `lib/streamers/providers/` (e.g. `mystreamer.py`)
2. Subclass `StreamerBase` and implement the required methods
3. That's it – the factory auto-discovers all subclasses

See [DEVELOPER.md](DEVELOPER.md) for a detailed guide with code examples.

## Project Structure

```
app.py                          # Flask app, routes, CLI entry point
lib/
├── streamers/
│   ├── streamerbase.py         # Abstract base class for all providers
│   ├── factory.py              # Auto-discovery & singleton registry
│   └── providers/              # One file per streaming service
├── utils/
│   ├── ttlcache.py             # Generic TTL cache
│   └── ffmpegwrapper.py        # Wrapper for future implementations
├── static/                     # Web UI assets + filler segments (*.ts)
└── templates/                  # Jinja2 templates
```

## Requirements

- Python 3.10+
- Flask, requests (see `requirements.txt`)
- FFmpeg (recommended, for `--ffmpeg` mode)
- Provider-specific dependencies are listed in separate `*.requirements.txt` files

## Known Issues

**HLS mode (default):**
- Without `--ad-filler`: ad breaks with different codec parameters can cause player crashes or stream stops
- With `--ad-filler`: ad segments are replaced with a static filler image; stream continues seamlessly
- `#EXT-X-ENDLIST` is always filtered to prevent stream stop at show boundaries

**`--ad-filler` mode (recommended for PlutoTV):**
- Tested stable with Kodi (`--kodi --ad-filler`) and VLC (`--ad-filler`)
- Ad detection patterns: `/creative/`, `_ad/`, `%2Fcreative%2F`, `_ad%2F`, `Pluto_TV_OandO`, `_ad_bumper_`
- Filler segment in `lib/static/` must match content codec parameters (H.264 720p, AAC 48kHz stereo)
- Default: `filler_blackwhite_fast.ts` – configurable via `PLUTO_FILLER_MEDIA_PATH` env var
- Included fillers: `filler.ts` (static image), `filler_blackwhite_quiet.ts`, `filler_blackwhite_lowvolume.ts`, `filler_blackwhite_fast.ts`
- Note: `--kodi` and `--ffmpeg` are mutually exclusive; `--kodi` takes precedence

**`--ffmpeg` mode (experimental):**
- Requires a working `ffmpeg` binary installed on the host system
- Copy mode cannot handle PlutoTV ad-break codec changes; re-encode mode (ultrafast) works but uses more CPU
- Watchdog (`--ffmpeg-timeout`) kills idle FFmpeg processes after the configured timeout (default: 30s)
## License

MIT
