"""
collectors/sharding.py
Sharded cluster view — only meaningful when connected to a mongos.

Chunk distribution: since 5.0 config.chunks is keyed by collection `uuid`
(older versions by `ns`), so chunks are grouped by both and mapped back to a
namespace through config.collections. Since 6.0 the balancer evens out DATA
SIZE, not chunk counts — the chunk spread is still the quickest visual
check, but the panel labels it as chunks, not as "imbalance" proof.

Cache keys: sharding.shards, sharding.balancer, sharding.collections,
            sharding.available/.reason
"""

from __future__ import annotations

from mongodb_brabo.collectors.base import BaseCollector, short_error


def summarize_chunks(groups: list[dict], collections: list[dict]) -> list[dict]:
    by_uuid = {str(c.get("uuid")): c for c in collections if c.get("uuid") is not None}
    by_ns = {c.get("_id"): c for c in collections}
    per_ns: dict[str, dict] = {}
    for g in groups:
        gid = g.get("_id") or {}
        ns = gid.get("ns")
        coll = None
        if gid.get("uuid") is not None:
            coll = by_uuid.get(str(gid["uuid"]))
            ns = ns or (coll or {}).get("_id")
        coll = coll or by_ns.get(ns)
        if not ns:
            continue
        row = per_ns.setdefault(ns, {"ns": ns, "key": (coll or {}).get("key", {}), "per_shard": {}, "total": 0})
        row["per_shard"][gid.get("shard")] = row["per_shard"].get(gid.get("shard"), 0) + g.get("n", 0)
        row["total"] += g.get("n", 0)
    out = []
    for row in per_ns.values():
        counts = list(row["per_shard"].values())
        row["max"], row["min"] = (max(counts), min(counts)) if counts else (0, 0)
        row["spread"] = row["max"] - row["min"]
        out.append(row)
    return sorted(out, key=lambda r: -r["total"])


class ShardingCollector(BaseCollector):
    def collect_sync(self) -> None:
        if not self.caps.is_mongos:
            self.unavailable("sharding", "Conexão não é um mongos — sharding só é visível conectando no roteador "
                                         f"(topologia atual: {self.caps.role_label}).")
            return
        if not self.caps.can_list_shards:
            self.unavailable("sharding", "Usuário sem a ação 'listShards' (precisa de clusterMonitor).")
            return
        try:
            shards = self.conn.command("admin", "listShards").get("shards") or []
        except Exception as exc:
            self.unavailable("sharding", f"listShards falhou: {short_error(exc)}")
            raise
        balancer = {}
        try:
            b = self.conn.command("admin", "balancerStatus")
            balancer = {"mode": b.get("mode"), "in_round": b.get("inBalancerRound"),
                        "rounds": b.get("numBalancerRounds"), "enabled": b.get("mode") == "full"}
        except Exception as exc:
            balancer = {"error": short_error(exc)}
        try:
            colls = self.conn.find("config", "collections", {"dropped": {"$ne": True}}, {"_id": 1, "uuid": 1, "key": 1})
            groups = self.conn.aggregate("config", [
                {"$group": {"_id": {"uuid": "$uuid", "ns": "$ns", "shard": "$shard"}, "n": {"$sum": 1}}}],
                collection="chunks")
            chunk_rows = summarize_chunks(groups, colls)
        except Exception as exc:
            chunk_rows = []
            balancer.setdefault("error", f"config.chunks: {short_error(exc)}")
        self.available("sharding")
        self.cache.set("sharding.shards", [{"id": s.get("_id"), "host": s.get("host"), "state": s.get("state"),
                                            "draining": s.get("draining", False), "tags": s.get("tags", [])}
                                           for s in shards], ttl=self.ttl)
        self.cache.set("sharding.balancer", balancer, ttl=self.ttl)
        self.cache.set("sharding.collections", chunk_rows, ttl=self.ttl)
