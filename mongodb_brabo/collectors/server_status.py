"""
collectors/server_status.py
One `serverStatus` per cycle feeds three panels: Dashboard (F1),
Locks/queues (F4) and WiredTiger (F9). A single round-trip is cheaper and
gives the three panels a consistent snapshot of the same instant.

Counters in serverStatus are cumulative since startup; rates (ops/s,
bytes/s, evictions/s) are computed from the delta between two consecutive
samples kept on the collector instance. The very first sample therefore has
no rates — reported as None (shown "—"), never as a made-up zero.

Cache keys: health.*, locks.*, wt.*
"""

from __future__ import annotations

import time

from mongodb_brabo.collectors.base import BaseCollector, short_error

_OPS = ("insert", "query", "update", "delete", "getmore", "command")


def _wt_cache(ss: dict) -> dict:
    c = ((ss.get("wiredTiger") or {}).get("cache")) or {}
    used = c.get("bytes currently in the cache")
    maxb = c.get("maximum bytes configured")
    dirty = c.get("tracked dirty bytes in the cache")
    out = {
        "bytes_in_cache": used, "max_bytes": maxb, "dirty_bytes": dirty,
        "read_into": c.get("bytes read into cache"),
        "written_from": c.get("bytes written from cache"),
        "evicted_app": c.get("pages evicted by application threads"),
        "evicted_modified": c.get("modified pages evicted"),
        "evicted_unmodified": c.get("unmodified pages evicted"),
        "used_pct": None, "dirty_pct": None,
    }
    if used is not None and maxb:
        out["used_pct"] = round(used / maxb * 100, 2)
    if dirty is not None and maxb:
        out["dirty_pct"] = round(dirty / maxb * 100, 2)
    return out


def _tickets(ss: dict) -> dict:
    """Pre-7.0: wiredTiger.concurrentTransactions. 7.0+: queues.execution.
    Read whichever block the server actually returned."""
    src = ((ss.get("queues") or {}).get("execution")) or \
        ((ss.get("wiredTiger") or {}).get("concurrentTransactions")) or {}
    out = {}
    for kind in ("read", "write"):
        k = src.get(kind) or {}
        out[kind] = {"out": k.get("out"), "available": k.get("available"), "total": k.get("totalTickets")}
    out["source"] = "queues.execution" if (ss.get("queues") or {}).get("execution") else (
        "wiredTiger.concurrentTransactions" if src else None)
    return out


def _checkpoint(ss: dict) -> dict:
    t = ((ss.get("wiredTiger") or {}).get("transaction")) or {}
    return {
        "count": t.get("transaction checkpoints"),
        "running": t.get("transaction checkpoint currently running"),
        "last_ms": t.get("transaction checkpoint most recent time (msecs)"),
        "max_ms": t.get("transaction checkpoint max time (msecs)"),
        "total_ms": t.get("transaction checkpoint total time (msecs)"),
    }


def _lock_stats(ss: dict) -> list[dict]:
    """Flatten serverStatus.locks into rows (resource, mode, counts)."""
    rows = []
    modes = {"r": "IS", "w": "IX", "R": "S", "W": "X"}
    for resource, stats in sorted((ss.get("locks") or {}).items()):
        if not isinstance(stats, dict):
            continue
        acq = stats.get("acquireCount") or {}
        wait = stats.get("acquireWaitCount") or {}
        tmicros = stats.get("timeAcquiringMicros") or {}
        dead = stats.get("deadlockCount") or {}
        for m in sorted(set(acq) | set(wait)):
            rows.append({
                "resource": resource, "mode": modes.get(m, m),
                "acquire": acq.get(m, 0) or 0, "wait": wait.get(m, 0) or 0,
                "wait_us": tmicros.get(m, 0) or 0, "deadlocks": dead.get(m, 0) or 0,
            })
    return rows


def summarize(ss: dict, prev: dict | None, dt: float | None) -> dict:
    """Pure transform: serverStatus (+ previous sample) → panel-ready dicts.
    Kept separate from I/O so tests can feed fabricated documents."""
    conns = ss.get("connections") or {}
    current = conns.get("current", 0) or 0
    available = conns.get("available", 0) or 0
    mem = ss.get("mem") or {}
    net = ss.get("network") or {}
    gl = ss.get("globalLock") or {}
    queue = gl.get("currentQueue") or {}
    active = gl.get("activeClients") or {}
    op = ss.get("opcounters") or {}
    wt = _wt_cache(ss)

    rates = {k: None for k in _OPS}
    rates.update({"total": None, "net_in": None, "net_out": None, "evictions": None, "requests": None})
    if prev and dt and dt > 0:
        pop = prev.get("opcounters") or {}
        total = 0.0
        for k in _OPS:
            d = max(0, (op.get(k, 0) or 0) - (pop.get(k, 0) or 0))
            rates[k] = d / dt
            total += d / dt
        rates["total"] = total
        pnet = prev.get("network") or {}
        rates["net_in"] = max(0, (net.get("bytesIn", 0) or 0) - (pnet.get("bytesIn", 0) or 0)) / dt
        rates["net_out"] = max(0, (net.get("bytesOut", 0) or 0) - (pnet.get("bytesOut", 0) or 0)) / dt
        rates["requests"] = max(0, (net.get("numRequests", 0) or 0) - (pnet.get("numRequests", 0) or 0)) / dt
        pwt = _wt_cache(prev)
        ev_now = sum((wt.get(k) or 0) for k in ("evicted_modified", "evicted_unmodified"))
        ev_prev = sum((pwt.get(k) or 0) for k in ("evicted_modified", "evicted_unmodified"))
        rates["evictions"] = max(0, ev_now - ev_prev) / dt
        rates["app_evictions"] = max(0, (wt.get("evicted_app") or 0) - (pwt.get("evicted_app") or 0)) / dt

    repl = ss.get("repl") or {}
    return {
        "identity": {
            "host": ss.get("host"), "version": ss.get("version"), "process": ss.get("process"),
            "pid": ss.get("pid"), "uptime_seconds": ss.get("uptime"),
            "set_name": repl.get("setName"), "is_primary": repl.get("isWritablePrimary", repl.get("ismaster")),
            "storage_engine": (ss.get("storageEngine") or {}).get("name"),
        },
        "connections": {
            "current": current, "available": available, "max": current + available,
            "active": conns.get("active"), "total_created": conns.get("totalCreated"),
            "used_pct": round(current / (current + available) * 100, 2) if (current + available) else None,
        },
        "opcounters": {k: op.get(k, 0) for k in _OPS},
        "rates": rates,
        "network": {"bytes_in": net.get("bytesIn"), "bytes_out": net.get("bytesOut"),
                    "requests": net.get("numRequests")},
        "memory": {"resident_mb": mem.get("resident"), "virtual_mb": mem.get("virtual"), "bits": mem.get("bits")},
        "queue": {"total": queue.get("total", 0), "readers": queue.get("readers", 0), "writers": queue.get("writers", 0)},
        "active_clients": {"total": active.get("total", 0), "readers": active.get("readers", 0),
                           "writers": active.get("writers", 0)},
        "lock_stats": _lock_stats(ss),
        "wt_cache": wt,
        "tickets": _tickets(ss),
        "checkpoint": _checkpoint(ss),
        "asserts": ss.get("asserts") or {},
    }


class ServerStatusCollector(BaseCollector):
    def __init__(self, *a, **kw) -> None:
        super().__init__(*a, **kw)
        self._prev: dict | None = None
        self._prev_t: float | None = None

    def collect_sync(self) -> None:
        if not self.caps.can_server_status:
            self.unavailable("health", "Usuário sem privilégio serverStatus (precisa de clusterMonitor).")
            return
        try:
            # metrics/repl subdocs are big and unused here; locks we do use.
            ss = self.conn.command("admin", {"serverStatus": 1, "metrics": 0})
        except Exception as exc:
            self.unavailable("health", f"serverStatus falhou: {short_error(exc)}")
            raise
        now = time.monotonic()
        dt = (now - self._prev_t) if self._prev_t else None
        s = summarize(ss, self._prev, dt)
        self._prev, self._prev_t = ss, now

        ttl = self.ttl
        self.available("health")
        self.cache.set("health.identity", s["identity"], ttl=60)
        self.cache.set("health.connections", s["connections"], ttl=ttl)
        self.cache.set("health.opcounters", s["opcounters"], ttl=ttl)
        self.cache.set("health.rates", s["rates"], ttl=ttl)
        self.cache.set("health.network", s["network"], ttl=ttl)
        self.cache.set("health.memory", s["memory"], ttl=ttl)
        self.cache.set("locks.queue", s["queue"], ttl=ttl)
        self.cache.set("locks.active_clients", s["active_clients"], ttl=ttl)
        self.cache.set("locks.stats", s["lock_stats"], ttl=ttl)
        self.cache.set("health.asserts", s["asserts"], ttl=ttl)
        if self.caps.has_wiredtiger or ss.get("wiredTiger"):
            self.available("wt")
            self.cache.set("wt.cache", s["wt_cache"], ttl=ttl)
            self.cache.set("wt.tickets", s["tickets"], ttl=ttl)
            self.cache.set("wt.checkpoint", s["checkpoint"], ttl=ttl)
        else:
            reason = "mongos não tem storage engine" if self.caps.is_mongos else \
                f"storage engine '{self.caps.storage_engine or '?'}' não é WiredTiger"
            self.unavailable("wt", reason)
            if s["tickets"]["source"]:
                self.cache.set("wt.tickets", s["tickets"], ttl=ttl)
