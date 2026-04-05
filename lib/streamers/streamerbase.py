from abc import ABC, abstractmethod
import requests
from datetime import datetime

class StreamerBase(ABC):
    def __init__(self, debug: bool = False, ip: str = "localhost", port: int = 7080, active: bool = True, ffmpeg: bool = False, ffmpeg_path: str = "ffmpeg", **kwargs):
        self.active         = active
        self.debug          = debug
        self.ffmpeg         = ffmpeg
        self.ffmpeg_path    = ffmpeg_path
        self.ip             = ip
        self.port           = port
        self.provider_name  = "NA" # Muss von ableitender Kalsse gesetzt werden
        self.USER_AGENT     = (
            "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
            "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/145.0.0.0 Safari/537.36"
        )
        self.http = requests.Session()
        self.http.headers.update({
            "User-Agent": self.USER_AGENT})

    def get_proxy_base_url(self):
        """ Gibt die Basis-URL zurück, unter der die Playlists und Streams dieses Streamers erreichbar sein werden.
        Beispiel: http://localhost:7080/plutotv """
        return f"http://{self.ip}:{self.port}/{self.provider_name.lower()}"

    def log(self, msg: str, force: bool = False):
        """Gibt msg aus - immer wenn force=True, sonst nur bei debug=True."""
        if force or self.debug:
            timestamp = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
            print(f"[{timestamp} - {self.provider_name}] {msg}")

    def print(self, msg: str):
        """Alias für log(msg, force=True)"""
        self.log(msg, force=True)

    @abstractmethod
    def boot(self): ...

    @abstractmethod
    def live_stream(self, channel_id: str): ...

    @abstractmethod
    def vod_stream(self, vod_id: str): ...
    
    @abstractmethod
    def playlist_m3u(self) -> str: ...
    
    @abstractmethod
    def get_epg_xml(self) -> str: ...

    @abstractmethod
    def get_channels(self): str: ...

    """ WIP - Implementation later - and optional """
    """
    @abstractmethod
    def get_vod_categories(self): str: ...

    @abstractmethod
    def get_vod_category_list(self, category_id: str): str: ...
    """