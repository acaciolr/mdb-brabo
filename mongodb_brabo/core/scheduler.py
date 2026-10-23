"""
core/scheduler.py
Drives all collectors on tiered intervals so a busy primary is never hit by
a thundering herd of monitoring commands. Each collector has its own loop
and its own exception isolation: one failing command (missing privilege,
timeout) never stops the others.

Collectors are synchronous (pymongo); `BaseCollector.collect()` offloads
them to worker threads, so the loops here are cheap asyncio tasks.
"""

from __future__ import annotations

import asyncio
import logging

from mongodb_brabo.collectors.objects import ObjectsCollector
from mongodb_brabo.collectors.profiler import ProfilerCollector
from mongodb_brabo.collectors.replication import ReplicationCollector
from mongodb_brabo.collectors.security import SecurityCollector
from mongodb_brabo.collectors.server_status import ServerStatusCollector
from mongodb_brabo.collectors.sessions import SessionsCollector
from mongodb_brabo.collectors.sharding import ShardingCollector

log = logging.getLogger(__name__)


class Scheduler:
    # Which collector(s) feed each panel — refreshed immediately on panel switch.
    PANEL_COLLECTORS = {
        "dashboard": ["ServerStatusCollector"],
        "sessions": ["SessionsCollector"],
        "slowops": ["ProfilerCollector", "SessionsCollector"],
        "locks": ["ServerStatusCollector", "SessionsCollector"],
        "databases": ["ObjectsCollector"],
        "indexes": ["ObjectsCollector"],
        "replication": ["ReplicationCollector"],
        "sharding": ["ShardingCollector"],
        "wiredtiger": ["ServerStatusCollector"],
        "oplog": ["ReplicationCollector"],
        "security": ["SecurityCollector"],
        "advisor": [],
        "history": [],
    }

    def __init__(self, conn_manager, cache, capabilities, refresh_interval: int = 5) -> None:
        self.cache = cache
        self._running = False
        self._tasks: list[asyncio.Task] = []
        self._inflight: set[str] = set()
        rt = max(1, min(refresh_interval, 5))
        fast = max(refresh_interval, 3)
        med = max(refresh_interval * 2, 10)
        slow = max(refresh_interval * 6, 60)
        args = (conn_manager, cache, capabilities)
        self.collectors = [
            ServerStatusCollector(*args, interval=rt),
            SessionsCollector(*args, interval=fast),
            ReplicationCollector(*args, interval=med),
            ProfilerCollector(*args, interval=med),
            ShardingCollector(*args, interval=med),
            SecurityCollector(*args, interval=slow),
            ObjectsCollector(*args, interval=slow),
        ]
        self._by_name = {c.__class__.__name__: c for c in self.collectors}

    def collect_now(self, panel_key: str) -> None:
        if not self._running:
            return
        for name in self.PANEL_COLLECTORS.get(panel_key, []):
            c = self._by_name.get(name)
            if c is not None:
                asyncio.create_task(self._safe_collect(c))

    async def run(self) -> None:
        self._running = True
        # Dashboard + Sessions first so F1 paints fast; the catalog walk last.
        await asyncio.gather(*(self._safe_collect(c) for c in self.collectors[:2]))
        await asyncio.gather(*(self._safe_collect(c) for c in self.collectors[2:]))
        self._tasks = [asyncio.create_task(self._loop(c), name=c.__class__.__name__) for c in self.collectors]
        await asyncio.gather(*self._tasks, return_exceptions=True)

    async def stop(self) -> None:
        self._running = False
        for t in self._tasks:
            t.cancel()
        await asyncio.gather(*self._tasks, return_exceptions=True)

    async def _safe_collect(self, collector) -> None:
        name = collector.__class__.__name__
        if name in self._inflight:
            return  # a slow cycle is still running — don't stack another on the server
        self._inflight.add(name)
        try:
            await collector.collect()
        except asyncio.CancelledError:
            raise
        except Exception as exc:
            log.warning("Collector %s error: %s", name, str(exc)[:300])
        finally:
            self._inflight.discard(name)

    async def _loop(self, collector) -> None:
        while self._running:
            try:
                await asyncio.sleep(collector.interval)
            except asyncio.CancelledError:
                break
            if self._running:
                await self._safe_collect(collector)
