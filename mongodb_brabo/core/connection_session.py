"""
core/connection_session.py
One ConnectionSession per tab: its own MongoClient, capability probe,
cache, scheduler and advisor. Nothing is shared between tabs except the
SQLite history file (which is keyed by host:port), so a dead or slow server
in one tab never affects another.
"""

from __future__ import annotations

import asyncio
import logging
import uuid

from mongodb_brabo.advisors.engine import AdvisorEngine
from mongodb_brabo.core.cache import MetricsCache
from mongodb_brabo.core.capabilities import Capabilities, discover
from mongodb_brabo.core.config import AppConfig
from mongodb_brabo.core.connection_manager import ConnectionManager
from mongodb_brabo.core.history_store import HistoryStore
from mongodb_brabo.core.scheduler import Scheduler

log = logging.getLogger(__name__)

_shared_history: HistoryStore | None = None


def history_store() -> HistoryStore:
    global _shared_history
    if _shared_history is None:
        _shared_history = HistoryStore()
    return _shared_history


def history_metrics(cache: MetricsCache) -> dict:
    """The handful of Dashboard metrics persisted to history.db."""
    conns = cache.get("health.connections") or {}
    rates = cache.get("health.rates") or {}
    wt = cache.get("wt.cache") or {}
    mem = cache.get("health.memory") or {}
    dbs = cache.get("database.list") or []
    data = sum((d.get("data_size") or 0) for d in dbs) if dbs else None
    storage = sum((d.get("size_on_disk") or 0) for d in dbs) if dbs else None
    oplog = cache.get("oplog.info") or {}
    return {
        "connections_current": conns.get("current"),
        "ops_per_sec": rates.get("total"),
        "wt_cache_used_pct": wt.get("used_pct"),
        "wt_cache_dirty_pct": wt.get("dirty_pct"),
        "resident_mb": mem.get("resident_mb"),
        "data_size_bytes": data,
        "storage_size_bytes": storage,
        "oplog_window_hours": oplog.get("window_hours"),
    }


class ConnectionSession:
    _HEALTH_INTERVAL = 15
    _HEALTH_THRESHOLD = 3
    _HISTORY_INTERVAL = 60

    def __init__(self, config: AppConfig) -> None:
        self.id = uuid.uuid4().hex[:8]
        self.config = config
        self.conn_manager = ConnectionManager(config)
        self.cache = MetricsCache()
        self.capabilities = Capabilities()
        self.scheduler: Scheduler | None = None
        self.advisor: AdvisorEngine | None = None
        self.connected = False
        self.is_healthy = True
        self._tasks: list[asyncio.Task] = []
        self._demo_runner = None
        self.history_key = config.history_key()

    @property
    def label(self) -> str:
        if self.config.demo:
            return self.config.label or "DEMO — mdb-brabo@localhost"
        if self.config.label:
            return self.config.label
        role = self.capabilities.role_label if self.connected else ""
        target = self.config.display_target() if self.config.uri else f"{self.config.host}:{self.config.port}"
        return f"{target} {role}".strip()

    async def connect(self) -> None:
        if self.config.demo:
            from mongodb_brabo.core.demo_data import DemoRunner
            self._demo_runner = DemoRunner(self.cache, interval=self.config.refresh_interval)
            self.capabilities = self._demo_runner.capabilities
            self.connected = True
            self.advisor = AdvisorEngine(self.cache, self.capabilities, interval=max(2, self.config.refresh_interval))
            self._tasks = [asyncio.create_task(self._demo_runner.run(), name=f"demo-{self.id}"),
                           asyncio.create_task(self.advisor.run(), name=f"adv-{self.id}")]
            return

        await self.conn_manager.connect()
        self.connected = True
        self.capabilities = await self.conn_manager.run(discover, self.conn_manager)
        self.cache.set("capabilities", self.capabilities.summary(), ttl=10 ** 9)
        self.scheduler = Scheduler(self.conn_manager, self.cache, self.capabilities, self.config.refresh_interval)
        self.advisor = AdvisorEngine(self.cache, self.capabilities)
        self._tasks = [
            asyncio.create_task(self.scheduler.run(), name=f"sched-{self.id}"),
            asyncio.create_task(self.advisor.run(), name=f"adv-{self.id}"),
            asyncio.create_task(self._health_loop(), name=f"health-{self.id}"),
            asyncio.create_task(self._history_loop(), name=f"hist-{self.id}"),
        ]
        log.info("Session %s connected: %s (MongoDB %s, %s)", self.id, self.label,
                 self.capabilities.version, self.capabilities.role_label)

    async def _history_loop(self) -> None:
        """Persist Dashboard metrics every 60s — from the cache only, zero
        extra load on the server."""
        await asyncio.sleep(30)
        store = history_store()
        while True:
            try:
                await asyncio.to_thread(store.record, self.history_key, history_metrics(self.cache))
            except asyncio.CancelledError:
                break
            except Exception as exc:
                log.warning("history snapshot failed: %s", exc)
            try:
                await asyncio.sleep(self._HISTORY_INTERVAL)
            except asyncio.CancelledError:
                break

    async def _health_loop(self) -> None:
        """pymongo reconnects by itself; this loop only drives the ⚠ marker
        on the tab so a dead server is visible even on a quiet panel."""
        failures = 0
        while True:
            try:
                await asyncio.sleep(self._HEALTH_INTERVAL)
                await self.conn_manager.run(self.conn_manager.command, "admin", "ping")
                failures = 0
                self.is_healthy = True
            except asyncio.CancelledError:
                break
            except Exception as exc:
                failures += 1
                log.warning("Session %s ping #%d failed: %s", self.id, failures, str(exc)[:200])
                if failures >= self._HEALTH_THRESHOLD:
                    self.is_healthy = False

    async def close(self) -> None:
        if self._demo_runner:
            self._demo_runner.stop()
        if self.scheduler:
            await self.scheduler.stop()
        if self.advisor:
            await self.advisor.stop()
        for t in self._tasks:
            t.cancel()
        await asyncio.gather(*self._tasks, return_exceptions=True)
        if not self.config.demo:
            await self.conn_manager.close()
        self.connected = False
