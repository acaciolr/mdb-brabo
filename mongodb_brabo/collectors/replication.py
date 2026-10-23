"""
collectors/replication.py
Replica set state (F7) and oplog (F10).

Lag is computed the way `rs.printSecondaryReplicationInfo()` does it:
primary optimeDate minus member optimeDate. With no primary (election in
progress, majority lost) the freshest member is used as reference and the
panel says so — a lag number without its reference point is misleading.

The oplog window is last.ts − first.ts of local.oplog.rs (two indexed
$natural reads, cheap). Generation rate is reported two ways:
  * average over the window (used bytes / window) — always available;
  * live rate from the delta of the oplog's used size between two samples,
    only while the capped collection hasn't wrapped yet (once full the size
    stops growing and a delta would wrongly say "0 B/s").

Cache keys: replication.*, oplog.*
"""

from __future__ import annotations

import time
from datetime import datetime, timezone

from mongodb_brabo.collectors.base import BaseCollector, short_error


def _aware(dt):
    if isinstance(dt, datetime) and dt.tzinfo is None:
        return dt.replace(tzinfo=timezone.utc)
    return dt


def _real_optime(m: dict):
    """Unreachable members are reported with optimeDate = 1970-01-01 (epoch).
    Treating that as an optime yields a ~56-year "lag" — it's "unknown"."""
    opt = _aware(m.get("optimeDate"))
    if not isinstance(opt, datetime) or opt.year < 2000 or m.get("health") == 0:
        return None
    return opt


def summarize_rs_status(st: dict, cfg: dict | None = None) -> dict:
    members_cfg = {m.get("host"): m for m in ((cfg or {}).get("members") or [])}
    members = st.get("members") or []
    primary = next((m for m in members if m.get("stateStr") == "PRIMARY"), None)
    data_members = [m for m in members if m.get("stateStr") != "ARBITER" and _real_optime(m)]
    ref = primary or (max(data_members, key=_real_optime) if data_members else None)
    ref_time = _real_optime(ref) if ref else None

    rows = []
    for m in members:
        opt = _real_optime(m)
        lag = None
        if ref_time and opt and m.get("stateStr") not in ("ARBITER",):
            lag = max(0.0, (ref_time - opt).total_seconds())
        c = members_cfg.get(m.get("name"), {})
        rows.append({
            "id": m.get("_id"), "name": m.get("name"), "state": m.get("stateStr"),
            "health": m.get("health"), "uptime": m.get("uptime"),
            "optime": opt.isoformat(sep=" ", timespec="seconds") if opt else "",
            "lag_seconds": lag, "sync_source": m.get("syncSourceHost") or m.get("syncingTo") or "",
            "ping_ms": m.get("pingMs"), "self": bool(m.get("self")),
            "last_heartbeat_msg": m.get("lastHeartbeatMessage") or "",
            "priority": c.get("priority"), "votes": c.get("votes"), "hidden": c.get("hidden", False),
            "election_date": _aware(m.get("electionDate")).isoformat(sep=" ", timespec="seconds")
            if m.get("electionDate") else "",
        })
    ecm = st.get("electionCandidateMetrics") or {}
    return {
        "set": st.get("set"), "my_state": st.get("myState"), "term": st.get("term"),
        "heartbeat_ms": st.get("heartbeatIntervalMillis"),
        "primary": primary.get("name") if primary else None,
        "lag_reference": "PRIMARY" if primary else ("membro mais recente" if ref else None),
        "last_election": _aware(ecm.get("lastElectionDate")).isoformat(sep=" ", timespec="seconds")
        if ecm.get("lastElectionDate") else "",
        "last_election_reason": ecm.get("lastElectionReason", ""),
        "members": rows,
        "max_lag_seconds": max((r["lag_seconds"] or 0 for r in rows if r["lag_seconds"] is not None), default=0),
        "unhealthy": [r["name"] for r in rows if r["health"] != 1 or r["state"] in
                      ("DOWN", "RECOVERING", "ROLLBACK", "UNKNOWN", "REMOVED", "(not reachable/healthy)")],
    }


class ReplicationCollector(BaseCollector):
    def __init__(self, *a, **kw) -> None:
        super().__init__(*a, **kw)
        self._prev_used: tuple[float, int] | None = None

    def collect_sync(self) -> None:
        if not self.caps.can_repl_status:
            if not self.caps.is_replset:
                reason = ("mongos — veja o painel F8 Sharding" if self.caps.is_mongos
                          else "Servidor STANDALONE — não faz parte de um replica set.")
            else:
                reason = "Usuário sem a ação 'replSetGetStatus' (precisa de clusterMonitor)."
            self.unavailable("replication", reason)
            self.unavailable("oplog", reason if not self.caps.is_replset else "Sem permissão de leitura do oplog.")
            return
        try:
            st = self.conn.command("admin", "replSetGetStatus")
        except Exception as exc:
            self.unavailable("replication", f"replSetGetStatus falhou: {short_error(exc)}")
            raise
        cfg = None
        try:
            cfg = (self.conn.command("admin", "replSetGetConfig") or {}).get("config")
        except Exception:
            pass  # priority/votes are nice-to-have; status alone is enough
        self.available("replication")
        self.cache.set("replication.status", summarize_rs_status(st, cfg), ttl=self.ttl)
        self._collect_oplog()

    def _collect_oplog(self) -> None:
        if not self.caps.can_read_oplog:
            self.unavailable("oplog", "Arbiter não guarda oplog.")
            return
        try:
            first = self.conn.find("local", "oplog.rs", {}, {"ts": 1, "wall": 1}, sort=[("$natural", 1)], limit=1)
            last = self.conn.find("local", "oplog.rs", {}, {"ts": 1, "wall": 1}, sort=[("$natural", -1)], limit=1)
            stats = self.conn.aggregate("local", [{"$collStats": {"storageStats": {}}}, {"$project": {
                "storageStats.size": 1, "storageStats.maxSize": 1, "storageStats.storageSize": 1,
                "storageStats.count": 1}}], collection="oplog.rs")
        except Exception as exc:
            self.unavailable("oplog", f"Leitura de local.oplog.rs falhou: {short_error(exc)}")
            return
        s = (stats[0].get("storageStats") if stats else {}) or {}
        info = summarize_oplog(first[0]["ts"] if first else None, last[0]["ts"] if last else None, s)
        now = time.monotonic()
        used = info.get("used_bytes")
        if self._prev_used and used is not None and info.get("max_bytes") and used < info["max_bytes"] * 0.98:
            dt = now - self._prev_used[0]
            if dt > 0 and used >= self._prev_used[1]:
                info["live_bytes_per_hour"] = (used - self._prev_used[1]) / dt * 3600
        if used is not None:
            self._prev_used = (now, used)
        self.available("oplog")
        self.cache.set("oplog.info", info, ttl=self.ttl)


def summarize_oplog(first_ts, last_ts, storage: dict) -> dict:
    """first_ts/last_ts are bson.Timestamp (or None)."""
    window = None
    first_s = last_s = ""
    if first_ts is not None and last_ts is not None:
        window = max(0, last_ts.time - first_ts.time)
        first_s = datetime.fromtimestamp(first_ts.time, timezone.utc).isoformat(sep=" ", timespec="seconds")
        last_s = datetime.fromtimestamp(last_ts.time, timezone.utc).isoformat(sep=" ", timespec="seconds")
    used = storage.get("size")
    maxb = storage.get("maxSize")
    avg_rate = (used / window * 3600) if (used and window) else None
    return {
        "first": first_s, "last": last_s, "window_seconds": window,
        "window_hours": round(window / 3600, 2) if window is not None else None,
        "used_bytes": used, "max_bytes": maxb, "storage_bytes": storage.get("storageSize"),
        "count": storage.get("count"),
        "used_pct": round(used / maxb * 100, 1) if (used is not None and maxb) else None,
        "avg_bytes_per_hour": avg_rate, "live_bytes_per_hour": None,
        # If the oplog isn't full yet, the window still grows: projected window
        # at the current average rate is maxSize / rate.
        "projected_window_hours": round(maxb / avg_rate, 1) if (maxb and avg_rate) else None,
    }
