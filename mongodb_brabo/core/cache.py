"""
core/cache.py
In-memory, thread-safe metrics cache with TTL and a history ring-buffer
(for sparklines/graphs). Collectors write here from worker threads; panels
read from the Textual event loop — hence the RLock.

Reads are stale-tolerant by default: once a key has a value it keeps being
returned after the TTL so panels never blank out between collector cycles.
Use is_fresh()/age() to detect and *show* staleness instead of hiding it.
"""

from __future__ import annotations

import time
from collections import deque
from dataclasses import dataclass
from threading import RLock
from typing import Any, Deque


@dataclass
class CacheEntry:
    value: Any
    timestamp: float
    ttl: float = 10.0

    @property
    def is_expired(self) -> bool:
        return time.monotonic() - self.timestamp > self.ttl


class MetricsCache:
    """Thread-safe cache for collector output, with per-key history."""

    HISTORY_SIZE = 180  # points per metric (180 x 5s = 15 min of live graph)

    def __init__(self) -> None:
        self._lock = RLock()
        self._data: dict[str, CacheEntry] = {}
        self._history: dict[str, Deque] = {}

    def set(self, key: str, value: Any, ttl: float = 10.0) -> None:
        with self._lock:
            now = time.monotonic()
            self._data[key] = CacheEntry(value=value, timestamp=now, ttl=ttl)
            self._history.setdefault(key, deque(maxlen=self.HISTORY_SIZE)).append((now, value))

    def get(self, key: str, default: Any = None, allow_stale: bool = True) -> Any:
        with self._lock:
            entry = self._data.get(key)
            if entry is None or (entry.is_expired and not allow_stale):
                return default
            return entry.value

    def get_history(self, key: str) -> list[tuple[float, Any]]:
        with self._lock:
            return list(self._history.get(key, []))

    def get_history_values(self, key: str) -> list[Any]:
        return [v for _, v in self.get_history(key)]

    def is_fresh(self, key: str) -> bool:
        with self._lock:
            entry = self._data.get(key)
            return entry is not None and not entry.is_expired

    def age(self, key: str) -> float | None:
        with self._lock:
            entry = self._data.get(key)
            return None if entry is None else time.monotonic() - entry.timestamp

    def keys(self) -> list[str]:
        with self._lock:
            return list(self._data.keys())

    def clear(self, key: str | None = None) -> None:
        with self._lock:
            if key:
                self._data.pop(key, None)
                self._history.pop(key, None)
            else:
                self._data.clear()
                self._history.clear()
