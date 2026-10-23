"""
core/history_store.py
Local, persisted metrics history — SQLite at ~/.mdb_brabo/history.db.
This is the only state that survives a restart; MetricsCache is an
in-memory ring buffer. History is written on a slow cadence (60s) from
metrics the Dashboard collector already fetched — it never queries MongoDB.

Sync API on purpose: callers wrap it in asyncio.to_thread. Every method
logs and degrades (returns empty) instead of raising.
"""

from __future__ import annotations

import logging
import sqlite3
import time
from pathlib import Path

from mongodb_brabo.core.paths import brabo_home

log = logging.getLogger(__name__)

_RETENTION_DAYS = 90


class HistoryStore:
    def __init__(self, db_path: Path | None = None) -> None:
        self.db_path = db_path or (brabo_home() / "history.db")
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        self._init_schema()

    def _connect(self) -> sqlite3.Connection:
        conn = sqlite3.connect(str(self.db_path), timeout=5.0)
        conn.execute("PRAGMA journal_mode=WAL")
        return conn

    def _init_schema(self) -> None:
        try:
            with self._connect() as conn:
                conn.execute(
                    "CREATE TABLE IF NOT EXISTS snapshots ("
                    " id INTEGER PRIMARY KEY AUTOINCREMENT,"
                    " connection_key TEXT NOT NULL, ts REAL NOT NULL,"
                    " metric TEXT NOT NULL, value REAL)"
                )
                conn.execute("CREATE INDEX IF NOT EXISTS idx_snap ON snapshots(connection_key, metric, ts)")
        except Exception as exc:
            log.warning("HistoryStore schema init failed: %s", exc)

    def record(self, conn_key: str, metrics: dict, ts: float | None = None) -> None:
        """None values are skipped — a gap is honest, a zero would be a lie."""
        ts = ts if ts is not None else time.time()
        rows = [(conn_key, ts, k, float(v)) for k, v in metrics.items() if v is not None]
        if not rows:
            return
        try:
            with self._connect() as conn:
                conn.executemany("INSERT INTO snapshots (connection_key, ts, metric, value) VALUES (?,?,?,?)", rows)
        except Exception as exc:
            log.warning("HistoryStore.record failed: %s", exc)

    def prune(self, older_than_days: int = _RETENTION_DAYS) -> None:
        try:
            with self._connect() as conn:
                conn.execute("DELETE FROM snapshots WHERE ts < ?", (time.time() - older_than_days * 86400,))
        except Exception as exc:
            log.warning("HistoryStore.prune failed: %s", exc)

    def query(self, conn_key: str, metric: str, since_ts: float) -> list[tuple[float, float]]:
        try:
            with self._connect() as conn:
                cur = conn.execute(
                    "SELECT ts, value FROM snapshots WHERE connection_key=? AND metric=? AND ts>=? ORDER BY ts",
                    (conn_key, metric, since_ts))
                return list(cur.fetchall())
        except Exception as exc:
            log.warning("HistoryStore.query failed: %s", exc)
            return []

    def available_metrics(self, conn_key: str) -> list[str]:
        try:
            with self._connect() as conn:
                cur = conn.execute("SELECT DISTINCT metric FROM snapshots WHERE connection_key=? ORDER BY metric",
                                   (conn_key,))
                return [r[0] for r in cur.fetchall()]
        except Exception as exc:
            log.warning("HistoryStore.available_metrics failed: %s", exc)
            return []

    def growth_projection(self, conn_key: str, metric: str, days_back: int = 30) -> dict:
        """Plain least-squares line. Returns {} with fewer than 2 points or
        less than 1 day of span — never extrapolates a trend from noise."""
        points = self.query(conn_key, metric, time.time() - days_back * 86400)
        if len(points) < 2:
            return {}
        xs = [p[0] for p in points]
        ys = [p[1] for p in points]
        if xs[-1] - xs[0] < 86400:
            return {}
        n = len(xs)
        mx, my = sum(xs) / n, sum(ys) / n
        denom = sum((x - mx) ** 2 for x in xs)
        if denom == 0:
            return {}
        slope = sum((x - mx) * (y - my) for x, y in zip(xs, ys)) / denom
        icpt = my - slope * mx
        now, day = time.time(), 86400
        return {
            "current": ys[-1], "slope_per_day": slope * day,
            "projected_30d": slope * (now + 30 * day) + icpt,
            "projected_90d": slope * (now + 90 * day) + icpt,
            "sample_count": n, "span_days": round((xs[-1] - xs[0]) / day, 1),
        }
