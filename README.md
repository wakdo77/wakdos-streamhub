# wakdos-streamhub ![Version](https://img.shields.io/badge/version-v0.6.6-blue)

A modular Python proxy that unifies multiple streaming services (IPTV, VOD, EPG) behind a single local API.

## What it does

wakdos-streamhub acts as a central hub between streaming providers and your media player. Instead of configuring each service separately, you point your player (Kodi, VLC, Jellyfin, etc.) at one local endpoint and get M3U playlists, live streams, and EPG data for all configured providers.

## Features

- **Unified API** – One base URL, consistent endpoints across all providers
- **M3U Playlists** – Ready-to-use playlists compatible with IPTV Simple Client, VLC, and Enigma2
- **XMLTV EPG** – Electronic program guide in standard XMLTV format
- **Live Streaming** – HLS proxy with automatic quality selection (not recommended, see known issues below)
- **FFmpeg Remux** – Recommended: MPEG-TS passthrough via FFmpeg (see known issues below)
- **Modular Providers** – Each streaming service is a self-contained plugin
- **Kodi Playlists** – Optional `--kodi` mode generates playlists with `inputstream.adaptive` props for native HLS handling in Kodi (better discontinuity support at ad breaks), not compatible with --ffmpeg
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

# Run (standard HLS mode)
python app.py --ip 192.168.178.65 --port 7000

# Default: Flask on all interfaces (0.0.0.0)
python app.py --ip 192.168.178.65 --port 7000

# Bind Flask to specific IP (default: 0.0.0.0 = all interfaces)
python app.py --ip 192.168.178.65 --flask-ip 192.168.178.65

# Run with FFmpeg remux (stutter-free, requires ffmpeg)
python app.py --ip 192.168.178.65 --port 7000 --ffmpeg --ffmpeg-path /usr/bin/ffmpeg

# Run with Kodi-compatible playlists (HLS only, do NOT combine with --ffmpeg)
python app.py --ip 192.168.178.65 --port 7000 --kodi
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
| `--kodi`           | `false`     | Generate Kodi-compatible playlist with `inputstream.adaptive` props (HLS only, incompatible with `--ffmpeg`) |

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
├── static/                     # Web UI assets
└── templates/                  # Jinja2 templates
```

## Requirements

- Python 3.10+
- Flask, requests (see `requirements.txt`)
- FFmpeg (recommended, for `--ffmpeg` mode)
- Provider-specific dependencies are listed in separate `*.requirements.txt` files

## Known Issues

**HLS mode (default, without `--ffmpeg`):**
- Can hang or fail around ad → next episode transitions for some providers (e.g. PlutoTV)
- Issues are typically related to how the provider signals end-of-stream or discontinuities in the HLS playlist

**`--ffmpeg` mode (recommended for PlutoTV):**
- Requires a working `ffmpeg` binary to be installed on the host system and accessible in `PATH` or explicitly set via `--ffmpeg-path`
- FFmpeg-based remuxing to MPEG-TS is currently more stable than plain HLS passthrough, especially around ad transitions
- Watchdog (`--ffmpeg-timeout`) will kill idle FFmpeg processes after the configured timeout (default: 30s)
- Remaining caveats:
  - short stalls are still possible on very messy ad/segment transitions
  - higher CPU usage compared to plain HLS mode
## License

MIT
