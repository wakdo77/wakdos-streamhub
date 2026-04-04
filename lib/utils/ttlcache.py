import time
from typing import Callable, Generic, TypeVar, Optional

T = TypeVar("T")

class TTLCache(Generic[T]):
    """Einfacher In-Memory-TTL-Cache pro Key (TTL in Minuten)."""

    def __init__(self, ttl_minutes: int):
        self.ttl_minutes = ttl_minutes
        self.ttl_seconds = ttl_minutes * 60
        self._values: dict[str, T] = {}
        self._timestamps: dict[str, float] = {}

    def get(self, key: str, loader: Callable[[], T]) -> T:
        now = time.time()
        if key in self._values:
            ts = self._timestamps.get(key, 0.0)
            if now - ts < self.ttl_seconds:
                return self._values[key]

        value = loader()
        self._values[key] = value
        self._timestamps[key] = now
        return value

    def invalidate(self, key: Optional[str] = None) -> None:
        """Key-spezifisch oder komplett invalidieren."""
        if key is None:
            self._values.clear()
            self._timestamps.clear()
        else:
            self._values.pop(key, None)
            self._timestamps.pop(key, None)
