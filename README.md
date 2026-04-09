# wakdos-streamhub ![Version](https://img.shields.io/badge/version-v0.6.1-blue)

A modular Python proxy that unifies multiple streaming services (IPTV, VOD, EPG) behind a single local API.

## What it does

wakdos-streamhub acts as a central hub between streaming providers and your media player. Instead of configuring each service separately, you point your player (Kodi, VLC, Jellyfin, etc.) at one local endpoint and get M3U playlists, live streams, and EPG data for all configured providers.

## Features

- **Unified API** – One base URL, consistent endpoints across all providers
- **M3U Playlists** – Ready-to-use playlists compatible with IPTV Simple Client, VLC, and Enigma2
- **XMLTV EPG** – Electronic program guide in standard XMLTV format
- **Live Streaming** – HLS proxy with automatic quality selection (best available)
- **FFmpeg Remux** – Optional MPEG-TS passthrough via FFmpeg (experimental – see known issues below)
- **Modular Providers** – Each streaming service is a self-contained plugin
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
python app.py --ip 0.0.0.0 --port 7000

# Run with FFmpeg remux (stutter-free, requires ffmpeg)
python app.py --ip 0.0.0.0 --port 7000 --ffmpeg --ffmpeg-path /usr/bin/ffmpeg
```

Then point your player at:
- **Playlist:** `http://<ip>:7000/plutotv/playlist.m3u`
- **EPG:** `http://<ip>:7000/plutotv/epg.xml`

### CLI Options

| Option          | Default     | Description                                    |
|-----------------|-------------|------------------------------------------------|
| `--ip`          | `localhost` | Bind address and playlist URL host             |
| `--port`        | `7000`      | Port                                           |
| `--debug`       | `false`     | Enable debug logging                           |
| `--ffmpeg`      | `false`     | FFmpeg remux for stutter-free streams          |
| `--ffmpeg-path` | `ffmpeg`    | Path to FFmpeg binary                          |
| `--flaskdebug`  | `false`     | Enable Flask-Debug                             |

## API Endpoints

All endpoints follow the pattern `/<provider>/...`:

| Endpoint                        | Description                                  |
|---------------------------------|----------------------------------------------|
| `GET /<provider>/playlist.m3u`  | M3U playlist (all channels)                  |
| `GET /<provider>/live/<id>`     | HLS live stream (or MPEG-TS with `--ffmpeg`) |
| `GET /<provider>/epg.xml`       | XMLTV EPG feed                               |
| `GET /<provider>/categories/`   | VOD categories (JSON)                        |
| `GET /<provider>/vod/<id>`      | VOD stream                                   |

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
- FFmpeg (optional, for `--ffmpeg` mode)
- Provider-specific dependencies are listed in separate `*.requirements.txt` files

## Known Issues

**`--ffmpeg` mode (experimental):**
- Video freezes at ad/content transitions due to codec parameter changes between segments
- May leave zombie FFmpeg processes on disconnect (especially on Windows)
- HLS mode (default, without `--ffmpeg`) is currently more stable and recommended

## License

Private project – not licensed for redistribution.
