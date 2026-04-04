from typing import Type, Dict

from .streamerbase import StreamerBase
from . import providers  # noqa: F401  # sorgt dafür, dass alle Provider-Module importiert werden


# Registry der Streamer-KLASSEN (key -> class)
_streamer_classes: Dict[str, Type[StreamerBase]] = {}
# Registry der Singleton-Instanzen (key -> instance)
_streamer_instances: Dict[str, StreamerBase] = {}
# Globale Startparameter (werden via configure() gesetzt)
_kwargs: dict = {}


def _build_class_registry() -> Dict[str, Type[StreamerBase]]:
    reg: Dict[str, Type[StreamerBase]] = {}
    for cls in StreamerBase.__subclasses__():
        key = getattr(cls, "key", cls.__name__.lower())
        if key in reg:
            raise RuntimeError(f"Duplicate streamer key detected: {key!r}")
        reg[key] = cls
    return reg


# wird beim Import einmalig gebaut
_streamer_classes = _build_class_registry()


def get_streamer_class(name: str) -> Type[StreamerBase]:
    try:
        return _streamer_classes[name]
    except KeyError:
        raise KeyError(
            f"Unknown streamer: {name!r}. Known streamers: {list(_streamer_classes.keys())}"
        )


def configure(**kwargs) -> None:
    """Setzt globale Startparameter (debug, ip, port) für alle Streamer-Instanzen."""
    global _kwargs
    _kwargs = kwargs


def get_streamer(name: str) -> StreamerBase:
    """Gibt die Singleton-Instanz für den gegebenen Streamer-Key zurück."""
    if name not in _streamer_instances:
        cls = get_streamer_class(name)
        _streamer_instances[name] = cls(**_kwargs)
    return _streamer_instances[name]


def all_streamer_classes() -> Dict[str, Type[StreamerBase]]:
    return dict(_streamer_classes)


def all_streamer_instances() -> Dict[str, StreamerBase]:
    # sorgt dafür, dass für alle Klassen Instanzen existieren
    for name in _streamer_classes:
        get_streamer(name)
    return dict(_streamer_instances)
