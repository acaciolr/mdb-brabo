"""
collectors/profiler.py
Top slow operations from `system.profile` — only where the database
profiler is actually on. MongoDB has no always-on statement statistics
(nothing like pg_stat_statements), so with the profiler off the honest
answer is "not collected" plus how to turn it on; the panel then falls back
to slow ops visible right now in $currentOp (sessions.list).

Reading system.profile is a plain find — it never changes the profiler
level. Turning the profiler on/off is an administrative action and goes
through ConnectionManager.execute_action() (F3 [P]/[O], DBA mode only).

Cache keys: profiler.levels, profiler.top, profiler.available/.reason
"""

from __future__ import annotations

from bson import json_util

from mongodb_brabo.collectors.base import BaseCollector, short_error

SKIP_DBS = {"local"}
TOP_PER_DB = 30
TOP_TOTAL = 60


def normalize_profile_doc(db: str, d: dict) -> dict:
    cmd = d.get("command") or d.get("query") or {}
    try:
        cmd_txt = json_util.dumps(cmd)
    except Exception:
        cmd_txt = str(cmd)
    ts = d.get("ts")
    return {
        "db": db, "ts": ts.isoformat(sep=" ", timespec="seconds") if hasattr(ts, "isoformat") else str(ts or ""),
        "op": d.get("op", ""), "ns": d.get("ns", ""), "millis": d.get("millis", 0) or 0,
        "docs_examined": d.get("docsExamined"), "keys_examined": d.get("keysExamined"),
        "nreturned": d.get("nreturned"), "plan_summary": d.get("planSummary", ""),
        "client": d.get("client", ""), "user": d.get("user", ""), "app_name": d.get("appName", ""),
        "command": cmd_txt[:4000],
    }


class ProfilerCollector(BaseCollector):
    def collect_sync(self) -> None:
        if not self.caps.can_profile:
            self.unavailable("profiler", "Profiler não existe em mongos/arbiter — conecte direto num mongod.")
            return
        try:
            dbs = [d["name"] for d in self.conn.command("admin", {"listDatabases": 1, "nameOnly": True})["databases"]]
        except Exception as exc:
            self.unavailable("profiler", f"listDatabases falhou: {short_error(exc)}")
            raise
        levels: dict[str, dict] = {}
        top: list[dict] = []
        errors: dict[str, str] = {}
        for db in dbs:
            if db in SKIP_DBS:
                continue
            try:
                st = self.conn.command(db, {"profile": -1})
                levels[db] = {"level": st.get("was", 0), "slowms": st.get("slowms"), "sample_rate": st.get("sampleRate")}
            except Exception as exc:
                errors[db] = short_error(exc)
                continue
            if levels[db]["level"] > 0:
                try:
                    docs = self.conn.find(db, "system.profile", {}, sort=[("millis", -1)], limit=TOP_PER_DB)
                    top.extend(normalize_profile_doc(db, d) for d in docs)
                except Exception as exc:
                    errors[db] = short_error(exc)
        top.sort(key=lambda r: -r["millis"])
        self.available("profiler")
        self.cache.set("profiler.levels", levels, ttl=self.ttl)
        self.cache.set("profiler.top", top[:TOP_TOTAL], ttl=self.ttl)
        self.cache.set("profiler.errors", errors, ttl=self.ttl)
