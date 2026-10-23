"""
collectors/base.py
A collector's only job: read from MongoDB through ConnectionManager and
write the result into MetricsCache. Collectors never touch the UI.

pymongo is synchronous, so each collector implements `collect_sync()` and
the async `collect()` merely offloads it to a worker thread. The same
`collect_sync()` is reused verbatim by the one-shot `--report` path, which
has no event loop to speak of.
"""

from __future__ import annotations

import logging
from abc import ABC, abstractmethod

from mongodb_brabo.core.cache import MetricsCache
from mongodb_brabo.core.capabilities import Capabilities


def short_error(exc: Exception) -> str:
    """pymongo errors embed the full server reply — keep the useful head."""
    msg = str(exc).split(", full error")[0]
    return msg[:240]


class BaseCollector(ABC):
    def __init__(self, conn_manager, cache: MetricsCache, capabilities: Capabilities, interval: int = 5) -> None:
        self.conn = conn_manager
        self.cache = cache
        self.caps = capabilities
        self.interval = interval
        self.log = logging.getLogger(self.__class__.__name__)

    @property
    def ttl(self) -> float:
        # Data is "fresh" for a bit over two cycles; after that the UI flags it as stale.
        return self.interval * 2 + 5

    @abstractmethod
    def collect_sync(self) -> None:
        """One collection cycle (blocking). Must not raise for 'expected'
        conditions such as missing privileges — record them instead."""

    async def collect(self) -> None:
        await self.conn.run(self.collect_sync)

    def unavailable(self, key: str, reason: str) -> None:
        """Mark a namespace as NOT AVAILABLE with an honest reason."""
        self.cache.set(f"{key}.available", False, ttl=self.ttl)
        self.cache.set(f"{key}.reason", reason, ttl=self.ttl)

    def available(self, key: str) -> None:
        self.cache.set(f"{key}.available", True, ttl=self.ttl)
        self.cache.set(f"{key}.reason", "", ttl=self.ttl)
