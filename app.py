import argparse
from flask import Flask, Response, abort, render_template, request
from lib.streamers.providers.plutotv import PlutoTV  # noqa: F401 – registriert PlutoTV in der Factory
import lib.streamers.factory as factory
from lib.streamers.factory import all_streamer_classes, get_streamer, all_streamer_instances


# ─── Konfiguration ───────────────────────────────────────────────────────────
__version__ = "0.6.6" # ad-break stability: fix ffmpeg routing, filter #EXT-X-DISCONTINUITY, add --kodi flag 


# Flask-App initialisieren
app = Flask("Streamer Proxy v" + __version__, static_folder="lib/static", template_folder="lib/templates")

# get all available streamers from lib/streamers/providers/
streamers = all_streamer_classes()
ffmpeg_timeout = 30


@app.route("/")
def index():
    return render_template("index.html", 
        version     = __version__, 
        streamers   = all_streamer_instances()
    )

@app.route("/<streamer_name>/playlist.m3u")
def playlist_m3u(streamer_name):
    streamer_name = streamer_name.lower()
    if streamer_name not in streamers:
        abort(404, description=f"Streamer {streamer_name} nicht gefunden. Verfügbare Streamer: {', '.join(streamers)}")

    # warning if ffmpeg and kodi playlist requested - not compatible
    if args.kodi is True:
        return get_streamer(streamer_name).playlist_m3u_kodi()

    return get_streamer(streamer_name).playlist_m3u()

@app.route("/<streamer_name>/live/<channel_id>", methods=["GET", "HEAD"])
@app.route("/<streamer_name>/live/<channel_id>.<ext>", methods=["GET", "HEAD"])
def live_stream(streamer_name: str, channel_id: str, ext: str = "m3u8"):
    streamer_name = streamer_name.lower()
    if streamer_name not in streamers:
        abort(404, description=f"Streamer {streamer_name} nicht gefunden. Verfügbare Streamer: {', '.join(streamers)}")

    # HEAD request: return headers only, don't spawn FFmpeg
    if request.method == "HEAD":
        mimetype = "video/MP2T" if ext == "ts" else "application/vnd.apple.mpegurl"
        return Response("", mimetype=mimetype)

    streamer = get_streamer(streamer_name)
    if ext == "ts" and streamer.ffmpeg:
        return streamer._live_stream_ffmpeg(channel_id, args.ffmpeg_timeout)
    return streamer._live_stream_hls(channel_id)

@app.route("/<streamer_name>/vod/<vod_id>")
@app.route("/<streamer_name>/vod/<vod_id>.m3u8")
def vod_stream(streamer_name: str, vod_id: str):
    streamer_name = streamer_name.lower()
    if streamer_name not in streamers:
        abort(404, description=f"Streamer {streamer_name} nicht gefunden. Verfügbare Streamer: {', '.join(streamers)}")
    pass

# returns json of general vod categories
@app.route("/<streamer_name>/categories/")
def get_categories(streamer_name: str):
    if streamer_name not in streamers:
        abort(404, description=f"Streamer {streamer_name} nicht gefunden. Verfügbare Streamer: {', '.join(streamers)}")
    
    vodstreamer = get_streamer(streamer_name)
    vodstreamer.get_vod_categories()
    return Response("ok")


@app.route("/<streamer_name>/epg.xml")
def epg_xml(streamer_name: str):
    streamer_name = streamer_name.lower()
    if streamer_name not in streamers:
        abort(404, description=f"Streamer {streamer_name} nicht gefunden. Verfügbare Streamer: {', '.join(streamers)}")
    return get_streamer(streamer_name).get_epg_xml()


# ─── Start ───────────────────────────────────────────────────────────────────

if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=f"Streamer-Proxy v{__version__}")
    parser.add_argument(
        "--ip",
        default="localhost",
        help="IP-Adresse für Playlist-URLs (default: localhost)",
    )
    parser.add_argument(
        "--flask-ip",
        default="0.0.0.0", # listen on all devices
        help="IP-Adress for flask app (default: 0.0.0.0)"
    )
    parser.add_argument(
        "--port",
        type=int,
        default=7000,
        help="Port der Flask-App (default: 7000)",
    )
    parser.add_argument(
        "--debug",
        action="store_true",
        help="Debug-Modus aktivieren",
    )
    parser.add_argument(
        "--flaskdebug",
        action="store_true",
        help="Debug-Modus für Flask aktivieren",
    )
    parser.add_argument(
        "--ffmpeg",
        action="store_true",
        help="FFmpeg-Remux für Live-Streams (löst Discontinuity-Stottern)",
    )
    parser.add_argument(
        "--ffmpeg-path",
        default="ffmpeg",
        help="Pfad zur FFmpeg-Binary (default: ffmpeg)",
    )
    parser.add_argument(
        "--ffmpeg-timeout",
        type=int,
        default="30",
        help="Watchdog Timeout for ffmpeg instances (default: 30)",
    )
    parser.add_argument(
        "--kodi",
        action="store_true",
        help="Kodi-kompatible Playlists generieren (nur für HLS-Streams, nicht mit FFmpeg-Remux)",
        default=False,
    )

    args = parser.parse_args()

    # configure ip, port for all streamers
    factory.configure(debug=args.debug, ip=args.ip, port=args.port, ffmpeg=args.ffmpeg, ffmpeg_path=args.ffmpeg_path)
    # configure ffmpeg timeout
    ffmpeg_timeout = args.ffmpeg_timeout

    proxy_base = f"http://{args.ip}:{args.port}"
    print(f"\n{'='*60}")
    print(f"  Streamer Proxy  v{__version__}")
    print(f"{'-'*60}")
    print(f"  Starte Stream-Proxy auf {proxy_base} ...")
    print(f"  Verfügbare Streamer: {', '.join(streamers)}")
    print("\n  Playlists:")
    for(streamers_name, streamer_cls) in streamers.items():
        print(f"   {streamer_cls.__name__} ({streamers_name})")
        print(f"    - {proxy_base}/{streamers_name}/playlist.m3u")
        print(f"    - {proxy_base}/{streamers_name}/epg.xml")
    print(f"\n  Debug           : {args.debug}")
    print(f"  FFmpeg          : {args.ffmpeg}")
    if args.ffmpeg:
        print(f"  FFmpeg Timeout  : {args.ffmpeg_timeout}")
    if args.kodi:
        print(f"  Kodi-Playlisten : {args.kodi} (nur ohne ffmpeg)")
    print(f"{'='*60}\n")
    app.run(host=args.flask_ip, port=args.port, threaded=not args.flaskdebug, debug=args.flaskdebug)