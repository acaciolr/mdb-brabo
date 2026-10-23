"""
core/demo_data.py
Simulated data for `--demo` (classes without a server, screenshots, tests).

This is the ONLY place in MDB BRABO allowed to invent numbers. Everything
it writes goes to the same cache keys real collectors use, plus the flag
`demo=True` that makes every panel show a "DEMO — DADOS SIMULADOS" banner,
and the tab label is prefixed "DEMO —". The advisor-relevant values are
deterministic (one secondary lagging 12s, unused indexes, TLS off) so tests
and classes see the same findings every time; only the live counters move.
"""

from __future__ import annotations

import asyncio
import random
import time
from datetime import datetime, timedelta, timezone

from mongodb_brabo.core.capabilities import Capabilities, TOPOLOGY_REPLSET


def demo_capabilities() -> Capabilities:
    return Capabilities(version="8.0.4-demo", major=8, minor=0, topology=TOPOLOGY_REPLSET,
                        set_name="rs-demo", is_primary=True, storage_engine="wiredTiger",
                        auth_enabled=True, users=["root_admin@admin"], roles=["root@admin"],
                        all_privileges=True)


class DemoRunner:
    def __init__(self, cache, interval: int = 5) -> None:
        self.cache = cache
        self.interval = interval
        self.capabilities = demo_capabilities()
        self._running = False
        self._rng = random.Random()
        self._ops = {"insert": 1_200_000, "query": 9_500_000, "update": 2_100_000,
                     "delete": 80_000, "getmore": 400_000, "command": 15_000_000}

    async def run(self) -> None:
        self._running = True
        while self._running:
            self.tick()
            await asyncio.sleep(self.interval)

    def stop(self) -> None:
        self._running = False

    def tick(self) -> None:
        c, r, ttl = self.cache, self._rng, self.interval * 3 + 5
        now = datetime.now(timezone.utc)
        c.set("demo", True, ttl=10 ** 9)
        c.set("capabilities", self.capabilities.summary(), ttl=10 ** 9)
        for ns in ("health", "sessions", "profiler", "database", "indexes", "replication",
                   "oplog", "sharding", "wt", "security"):
            c.set(f"{ns}.available", True, ttl=ttl)
            c.set(f"{ns}.reason", "", ttl=ttl)

        cur = r.randint(80, 140)
        c.set("health.identity", {"host": "demo-node-01:27017", "version": "8.0.4-demo", "process": "mongod",
                                  "pid": 4242, "uptime_seconds": 3 * 86400 + int(time.time()) % 86400,
                                  "set_name": "rs-demo", "is_primary": True, "storage_engine": "wiredTiger"}, ttl=ttl)
        c.set("health.connections", {"current": cur, "available": 838_860 - cur, "max": 838_860,
                                     "active": r.randint(5, 25), "total_created": 52_340,
                                     "used_pct": round(cur / 838_860 * 100, 3)}, ttl=ttl)
        rates = {"insert": r.uniform(50, 400), "query": r.uniform(300, 1500), "update": r.uniform(80, 500),
                 "delete": r.uniform(0, 20), "getmore": r.uniform(10, 80), "command": r.uniform(400, 1200)}
        rates["total"] = sum(rates.values())
        rates.update({"net_in": r.uniform(2e5, 9e5), "net_out": r.uniform(1e6, 6e6),
                      "requests": r.uniform(900, 3000), "evictions": r.uniform(0, 50), "app_evictions": 0.0})
        for k in self._ops:
            self._ops[k] += int(rates[k] * self.interval)
        c.set("health.rates", rates, ttl=ttl)
        c.set("health.opcounters", dict(self._ops), ttl=ttl)
        c.set("health.network", {"bytes_in": 9_800_000_000, "bytes_out": 51_000_000_000, "requests": 88_000_000}, ttl=ttl)
        c.set("health.memory", {"resident_mb": 3100 + r.randint(0, 80), "virtual_mb": 5200, "bits": 64}, ttl=ttl)
        c.set("health.asserts", {"regular": 0, "warning": 0, "msg": 0, "user": 37, "rollovers": 0}, ttl=ttl)

        qr, qw = r.choice([0, 0, 0, 1, 2]), r.choice([0, 0, 1])
        c.set("locks.queue", {"total": qr + qw, "readers": qr, "writers": qw}, ttl=ttl)
        c.set("locks.active_clients", {"total": r.randint(4, 20), "readers": r.randint(2, 10),
                                       "writers": r.randint(1, 6)}, ttl=ttl)
        c.set("locks.stats", [
            {"resource": "Global", "mode": "IS", "acquire": 48_000_000, "wait": 12, "wait_us": 9_500, "deadlocks": 0},
            {"resource": "Global", "mode": "IX", "acquire": 6_100_000, "wait": 30, "wait_us": 41_000, "deadlocks": 0},
            {"resource": "Database", "mode": "IX", "acquire": 6_000_000, "wait": 3, "wait_us": 1_200, "deadlocks": 0},
            {"resource": "Collection", "mode": "X", "acquire": 210, "wait": 1, "wait_us": 350_000, "deadlocks": 0},
        ], ttl=ttl)

        maxb = 8 * 1024 ** 3
        used_pct = r.uniform(72, 81)
        dirty_pct = r.uniform(1.0, 4.5)
        c.set("wt.cache", {"bytes_in_cache": int(maxb * used_pct / 100), "max_bytes": maxb,
                           "dirty_bytes": int(maxb * dirty_pct / 100), "used_pct": round(used_pct, 2),
                           "dirty_pct": round(dirty_pct, 2), "read_into": 912_000_000_000,
                           "written_from": 403_000_000_000, "evicted_app": 0,
                           "evicted_modified": 1_830_000, "evicted_unmodified": 9_200_000}, ttl=ttl)
        c.set("wt.tickets", {"read": {"out": r.randint(0, 6), "available": 122, "total": 128},
                             "write": {"out": r.randint(0, 3), "available": 125, "total": 128},
                             "source": "queues.execution"}, ttl=ttl)
        c.set("wt.checkpoint", {"count": 4_300, "running": 0, "last_ms": r.randint(300, 1400),
                                "max_ms": 6_800, "total_ms": 3_100_000}, ttl=ttl)

        ops = [
            {"opid": 9001, "shard": None, "type": "op", "active": True, "op": "query", "ns": "loja.pedidos",
             "secs_running": 95, "microsecs_running": 95_000_000, "client": "10.0.0.21:51022",
             "app_name": "relatorio-mensal", "user": "app_rel", "desc": "conn812", "waiting_for_lock": False,
             "plan_summary": "COLLSCAN", "num_yields": 7400,
             "command": '{"find": "pedidos", "filter": {"status": "ABERTO", "valor": {"$gt": 1000}}}',
             "internal": False},
            {"opid": 9002, "shard": None, "type": "op", "active": True, "op": "update", "ns": "loja.estoque",
             "secs_running": 3, "microsecs_running": 3_100_000, "client": "10.0.0.22:50110",
             "app_name": "api-estoque", "user": "app_api", "desc": "conn901", "waiting_for_lock": r.choice([False, True]),
             "plan_summary": "IXSCAN { sku: 1 }", "num_yields": 2,
             "command": '{"update": "estoque", "updates": [{"q": {"sku": "X-1"}, "u": {"$inc": {"qtd": -1}}}]}',
             "internal": False},
            {"opid": 9003, "shard": None, "type": "op", "active": True, "op": "getmore", "ns": "local.oplog.rs",
             "secs_running": 0, "microsecs_running": 400, "client": "192.168.60.2:41200", "app_name": "",
             "user": "__system", "desc": "conn12", "waiting_for_lock": False, "plan_summary": "",
             "num_yields": 0, "command": '{"getMore": 1, "collection": "oplog.rs"}', "internal": True},
        ]
        c.set("sessions.list", ops, ttl=ttl)
        c.set("sessions.counts", {"total": len(ops), "user_active": 2, "waiting_lock": sum(o["waiting_for_lock"] for o in ops),
                                  "long_running": 1, "longest_secs": 95}, ttl=ttl)

        c.set("profiler.levels", {"admin": {"level": 0, "slowms": 100}, "config": {"level": 0, "slowms": 100},
                                  "loja": {"level": 1, "slowms": 100}, "rh": {"level": 0, "slowms": 100}}, ttl=ttl)
        c.set("profiler.top", [
            {"db": "loja", "ts": (now - timedelta(minutes=3)).isoformat(sep=" ", timespec="seconds"), "op": "query",
             "ns": "loja.pedidos", "millis": 2840, "docs_examined": 1_250_000, "keys_examined": 0, "nreturned": 312,
             "plan_summary": "COLLSCAN", "client": "10.0.0.21", "user": "app_rel@loja", "app_name": "relatorio-mensal",
             "command": '{"find": "pedidos", "filter": {"status": "ABERTO"}}'},
            {"db": "loja", "ts": (now - timedelta(minutes=9)).isoformat(sep=" ", timespec="seconds"), "op": "command",
             "ns": "loja.clientes", "millis": 640, "docs_examined": 80_000, "keys_examined": 80_000, "nreturned": 20,
             "plan_summary": "IXSCAN { cidade: 1 }", "client": "10.0.0.30", "user": "app_api@loja",
             "app_name": "api-clientes", "command": '{"aggregate": "clientes", "pipeline": [{"$group": "..."}]}'},
        ], ttl=ttl)
        c.set("profiler.errors", {}, ttl=ttl)

        gb = 1024 ** 3
        c.set("database.list", [
            {"name": "loja", "size_on_disk": int(14.2 * gb), "empty": False, "collections": 6, "views": 1,
             "objects": 18_400_000, "data_size": int(21 * gb), "storage_size": int(11 * gb), "indexes": 14,
             "index_size": int(3.1 * gb), "avg_obj_size": 1224, "error": ""},
            {"name": "rh", "size_on_disk": int(0.6 * gb), "empty": False, "collections": 3, "views": 0,
             "objects": 410_000, "data_size": int(0.9 * gb), "storage_size": int(0.5 * gb), "indexes": 5,
             "index_size": int(0.08 * gb), "avg_obj_size": 2300, "error": ""},
            {"name": "admin", "size_on_disk": 300_000, "empty": False, "collections": 3, "views": 0, "objects": 12,
             "data_size": 9_000, "storage_size": 90_000, "indexes": 4, "index_size": 120_000, "avg_obj_size": 750,
             "error": ""},
        ], ttl=ttl)
        c.set("database.collections", [
            {"db": "loja", "name": "pedidos", "ns": "loja.pedidos", "count": 12_500_000, "size": int(15 * gb),
             "storage_size": int(7.8 * gb), "total_index_size": int(2.2 * gb), "nindexes": 5,
             "avg_obj_size": 1288, "capped": False, "error": ""},
            {"db": "loja", "name": "clientes", "ns": "loja.clientes", "count": 5_800_000, "size": int(5.6 * gb),
             "storage_size": int(2.9 * gb), "total_index_size": int(0.8 * gb), "nindexes": 4,
             "avg_obj_size": 1036, "capped": False, "error": ""},
            {"db": "rh", "name": "funcionarios", "ns": "rh.funcionarios", "count": 410_000, "size": int(0.9 * gb),
             "storage_size": int(0.5 * gb), "total_index_size": int(0.08 * gb), "nindexes": 3,
             "avg_obj_size": 2300, "capped": False, "error": ""},
        ], ttl=ttl)
        c.set("database.truncated", False, ttl=ttl)

        since = (now - timedelta(days=3)).isoformat(sep=" ", timespec="seconds")
        idx = [
            {"db": "loja", "collection": "pedidos", "ns": "loja.pedidos", "name": "_id_", "key": {"_id": 1},
             "ops": 8_100_000, "since": since, "tracked_hours": 72.0, "size": int(0.6 * gb), "flags": "unique",
             "building": False, "unused": False},
            {"db": "loja", "collection": "pedidos", "ns": "loja.pedidos", "name": "cliente_1_data_-1",
             "key": {"cliente": 1, "data": -1}, "ops": 2_400_000, "since": since, "tracked_hours": 72.0,
             "size": int(0.9 * gb), "flags": "", "building": False, "unused": False},
            {"db": "loja", "collection": "pedidos", "ns": "loja.pedidos", "name": "obs_1", "key": {"obs": 1},
             "ops": 0, "since": since, "tracked_hours": 72.0, "size": int(0.4 * gb), "flags": "sparse",
             "building": False, "unused": True},
            {"db": "loja", "collection": "clientes", "ns": "loja.clientes", "name": "cpf_1", "key": {"cpf": 1},
             "ops": 930_000, "since": since, "tracked_hours": 72.0, "size": int(0.3 * gb), "flags": "unique",
             "building": False, "unused": False},
            {"db": "rh", "collection": "funcionarios", "ns": "rh.funcionarios", "name": "setor_1_cargo_1",
             "key": {"setor": 1, "cargo": 1}, "ops": 0, "since": since, "tracked_hours": 72.0,
             "size": int(0.02 * gb), "flags": "", "building": False, "unused": True},
        ]
        c.set("indexes.list", idx, ttl=ttl)
        c.set("indexes.unused", [i for i in idx if i["unused"]], ttl=ttl)

        ts = (now - timedelta(seconds=1)).isoformat(sep=" ", timespec="seconds")
        members = [
            {"id": 0, "name": "demo-node-01:27017", "state": "PRIMARY", "health": 1, "uptime": 259_200,
             "optime": ts, "lag_seconds": 0.0, "sync_source": "", "ping_ms": None, "self": True,
             "last_heartbeat_msg": "", "priority": 2, "votes": 1, "hidden": False,
             "election_date": (now - timedelta(days=3)).isoformat(sep=" ", timespec="seconds")},
            {"id": 1, "name": "demo-node-02:27017", "state": "SECONDARY", "health": 1, "uptime": 259_000,
             "optime": ts, "lag_seconds": 1.0, "sync_source": "demo-node-01:27017", "ping_ms": 1,
             "self": False, "last_heartbeat_msg": "", "priority": 1, "votes": 1, "hidden": False, "election_date": ""},
            {"id": 2, "name": "demo-node-03:27017", "state": "SECONDARY", "health": 1, "uptime": 258_000,
             "optime": ts, "lag_seconds": 12.0, "sync_source": "demo-node-02:27017", "ping_ms": 3,
             "self": False, "last_heartbeat_msg": "", "priority": 1, "votes": 1, "hidden": False, "election_date": ""},
        ]
        c.set("replication.status", {"set": "rs-demo", "my_state": 1, "term": 7, "heartbeat_ms": 2000,
                                     "primary": "demo-node-01:27017", "lag_reference": "PRIMARY",
                                     "last_election": members[0]["election_date"],
                                     "last_election_reason": "electionTimeout", "members": members,
                                     "max_lag_seconds": 12.0, "unhealthy": []}, ttl=ttl)
        maxo = 2 * gb
        c.set("oplog.info", {"first": (now - timedelta(hours=61)).isoformat(sep=" ", timespec="seconds"),
                             "last": ts, "window_seconds": 61 * 3600, "window_hours": 61.0,
                             "used_bytes": int(maxo * 0.99), "max_bytes": maxo, "storage_bytes": int(0.8 * gb),
                             "count": 6_400_000, "used_pct": 99.0, "avg_bytes_per_hour": maxo * 0.99 / 61,
                             "live_bytes_per_hour": None, "projected_window_hours": 61.6}, ttl=ttl)

        c.set("sharding.shards", [
            {"id": "shard-a", "host": "rs-a/demo-sa1:27018,demo-sa2:27018", "state": 1, "draining": False, "tags": []},
            {"id": "shard-b", "host": "rs-b/demo-sb1:27018,demo-sb2:27018", "state": 1, "draining": False, "tags": []},
        ], ttl=ttl)
        c.set("sharding.balancer", {"mode": "full", "in_round": False, "rounds": 1834, "enabled": True}, ttl=ttl)
        c.set("sharding.collections", [
            {"ns": "loja.pedidos", "key": {"cliente": "hashed"}, "per_shard": {"shard-a": 64, "shard-b": 61},
             "total": 125, "max": 64, "min": 61, "spread": 3},
        ], ttl=ttl)

        c.set("security.options", {"known": True, "authorization": "enabled", "auth_enabled": True,
                                   "key_file": True, "cluster_auth_mode": "keyFile", "tls_mode": "disabled",
                                   "bind_ip": "0.0.0.0", "port": 27017, "audit_log": False,
                                   "javascript_enabled": True, "argv": "mongod --config /etc/mongod.conf",
                                   "config_file": "/etc/mongod.conf"}, ttl=ttl)
        c.set("security.users", [
            {"user": "root_admin", "db": "admin", "roles": ["root@admin"], "mechanisms": ["SCRAM-SHA-256"]},
            {"user": "monitor", "db": "admin", "roles": ["clusterMonitor@admin"], "mechanisms": ["SCRAM-SHA-256"]},
            {"user": "app_api", "db": "loja", "roles": ["readWrite@loja"], "mechanisms": ["SCRAM-SHA-1", "SCRAM-SHA-256"]},
        ], ttl=ttl)
        c.set("security.roles", [{"role": "relatorios", "db": "admin", "inherits": ["read@loja"]}], ttl=ttl)
        c.set("security.mechanisms", ["SCRAM-SHA-1", "SCRAM-SHA-256"], ttl=ttl)
        c.set("security.errors", {}, ttl=ttl)
        c.set("security.session", {"users": ["root_admin@admin"], "roles": ["root@admin"]}, ttl=ttl)
