"""
widgets/connection_pane.py
Hosts every panel of ONE ConnectionSession (one tab). Only the visible
panel is ticked, so hidden panels cost nothing.
"""
from __future__ import annotations

import logging

from textual.app import ComposeResult
from textual.widget import Widget

from mongodb_brabo.widgets.panels import AdvisorPanel, DashboardPanel, LocksPanel, SessionsPanel, SlowOpsPanel
from mongodb_brabo.widgets.panels_cluster import ReplicationPanel, SecurityPanel, ShardingPanel
from mongodb_brabo.widgets.panels_data import DatabasesPanel, IndexesPanel, OplogPanel, WiredTigerPanel
from mongodb_brabo.widgets.panels_history import HistoryPanel

log = logging.getLogger(__name__)

# (key, class, cache key that tells the subtitle whether data is fresh)
PANELS: list[tuple[str, type, str]] = [
    ("dashboard", DashboardPanel, "health.connections"),
    ("sessions", SessionsPanel, "sessions.list"),
    ("slowops", SlowOpsPanel, "profiler.levels"),
    ("locks", LocksPanel, "locks.queue"),
    ("databases", DatabasesPanel, "database.list"),
    ("indexes", IndexesPanel, "indexes.list"),
    ("replication", ReplicationPanel, "replication.status"),
    ("sharding", ShardingPanel, "sharding.shards"),
    ("wiredtiger", WiredTigerPanel, "wt.cache"),
    ("oplog", OplogPanel, "oplog.info"),
    ("security", SecurityPanel, "security.options"),
    ("advisor", AdvisorPanel, "advisor.findings"),
    ("history", HistoryPanel, "health.connections"),
]
PANEL_KEYS = [p[0] for p in PANELS]
_PRIMARY = {k: key for k, _, key in PANELS}


class ConnectionPane(Widget):
    DEFAULT_CSS = "ConnectionPane { height: 1fr; width: 1fr; }"

    def __init__(self, session, **kwargs) -> None:
        super().__init__(**kwargs)
        self.session = session
        self.active_panel = "dashboard"

    def compose(self) -> ComposeResult:
        sid = self.session.id
        for idx, (name, cls, _) in enumerate(PANELS):
            yield cls(self.session, id=f"panel-{name}-{sid}", classes="" if idx == 0 else "hidden")

    def show_panel(self, panel: str) -> None:
        sid = self.session.id
        try:
            self.query_one(f"#panel-{self.active_panel}-{sid}").add_class("hidden")
            new = self.query_one(f"#panel-{panel}-{sid}")
            new.remove_class("hidden")
            self.active_panel = panel
            new._last_refresh = 0  # repaint immediately with what's cached
            if self.session.scheduler is not None:
                self.session.scheduler.collect_now(panel)
        except Exception as exc:
            log.warning("Panel switch error: %s", exc)

    def active_primary_key(self) -> str:
        return _PRIMARY.get(self.active_panel, "health.connections")

    async def refresh_active_panel(self) -> None:
        try:
            await self.query_one(f"#panel-{self.active_panel}-{self.session.id}").tick()
        except Exception as exc:
            log.warning("Refresh error: %s", exc)
