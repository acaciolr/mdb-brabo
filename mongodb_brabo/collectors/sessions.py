"""
collectors/sessions.py
In-progress operations via the `$currentOp` aggregation stage (3.6+), with
the legacy `currentOp` command as fallback for older servers.

Not every in-progress op is a "user session": replication fetchers tailing
local.oplog.rs, awaitable hello/isMaster (drivers park those for ~10s on
purpose), change streams and nameless background threads would all look
like "long running operations" if taken at face value. They are flagged
`internal` so the panel can dim them and the Advisor ignores them.

Cache keys: sessions.list, sessions.counts, sessions.available/.reason
"""

from __future__ import annotations

from bson import json_util

from mongodb_brabo.collectors.base import BaseCollector, short_error

_AWAITABLE = ("hello", "isMaster", "ismaster")
LONG_OP_SECONDS = 60


def _client(op: dict) -> str:
    return op.get("client") or op.get("client_s") or ""


def is_internal(op: dict) -> bool:
    cmd = op.get("command") or {}
    ns = op.get("ns") or ""
    if ns.startswith("local.oplog") or ns.startswith("local.replset"):
        return True
    if any(k in cmd for k in _AWAITABLE) and "maxAwaitTimeMS" in cmd:
        return True
    if "$currentOp" in str(cmd.get("pipeline", ""))[:200]:
        return True  # our own monitoring query
    if "$changeStream" in str(cmd.get("pipeline", ""))[:200]:
        return True
    if not _client(op) and not op.get("effectiveUsers"):
        return True  # server-internal thread (TTLMonitor, Checkpointer, ...)
    return False


def normalize(op: dict) -> dict:
    cmd = op.get("command") or {}
    try:
        cmd_txt = json_util.dumps(cmd)
    except Exception:
        cmd_txt = str(cmd)
    users = op.get("effectiveUsers") or []
    user = users[0].get("user") if users else ""
    return {
        "opid": op.get("opid"),
        "shard": op.get("shard"),
        "type": op.get("type"),
        "active": bool(op.get("active")),
        "op": op.get("op", ""),
        "ns": op.get("ns", ""),
        "secs_running": op.get("secs_running") or (op.get("microsecs_running", 0) or 0) // 1_000_000,
        "microsecs_running": op.get("microsecs_running"),
        "client": _client(op),
        "app_name": op.get("appName", ""),
        "user": user or "",
        "desc": op.get("desc", ""),
        "waiting_for_lock": bool(op.get("waitingForLock")),
        "plan_summary": op.get("planSummary", ""),
        "num_yields": op.get("numYields"),
        "command": cmd_txt[:4000],
        "internal": is_internal(op),
    }


def count_ops(ops: list[dict]) -> dict:
    user_ops = [o for o in ops if not o["internal"]]
    return {
        "total": len(ops),
        "user_active": sum(1 for o in user_ops if o["active"]),
        "waiting_lock": sum(1 for o in ops if o["waiting_for_lock"]),
        "long_running": sum(1 for o in user_ops if o["active"] and (o["secs_running"] or 0) >= LONG_OP_SECONDS),
        "longest_secs": max((o["secs_running"] or 0 for o in user_ops if o["active"]), default=0),
    }


class SessionsCollector(BaseCollector):
    MAX_OPS = 500

    def collect_sync(self) -> None:
        if not self.caps.can_current_op:
            self.unavailable("sessions", "Usuário sem a ação 'inprog' (precisa de clusterMonitor) — $currentOp indisponível.")
            return
        try:
            if self.caps.major >= 4 or (self.caps.major == 3 and self.caps.minor >= 6) or self.caps.major == 0:
                raw = self.conn.aggregate("admin", [
                    {"$currentOp": {"allUsers": True, "idleConnections": False}},
                    {"$sort": {"microsecs_running": -1}},
                ], limit=self.MAX_OPS)
            else:
                raw = (self.conn.command("admin", {"currentOp": 1}).get("inprog") or [])[: self.MAX_OPS]
        except Exception as exc:
            self.unavailable("sessions", f"$currentOp falhou: {short_error(exc)}")
            raise
        ops = sorted((normalize(o) for o in raw), key=lambda o: (o["internal"], -(o["secs_running"] or 0)))
        self.available("sessions")
        self.cache.set("sessions.list", ops, ttl=self.ttl)
        self.cache.set("sessions.counts", count_ops(ops), ttl=self.ttl)
