"""
collectors/objects.py
Databases, collections and indexes — feeds F5 (Bancos/Coleções) and F6
(Índices) from ONE walk over the catalog, because both need the same
per-collection `$collStats` (sizes, index sizes) and walking it twice on a
server with hundreds of collections is exactly the overhead a monitoring
tool must not add.

Bounded on purpose: at most MAX_COLLECTIONS collections are inspected per
cycle (largest databases first), and the cycle runs on the "slow" tier.
Through mongos, $collStats/$indexStats return one document per shard; they
are summed per collection/index.

Cache keys: database.list, database.collections, database.truncated,
            indexes.list, indexes.unused, indexes.available/.reason
"""

from __future__ import annotations

from datetime import datetime, timezone

from mongodb_brabo.collectors.base import BaseCollector, short_error

SYSTEM_DBS = {"admin", "local", "config"}
MAX_COLLECTIONS = 300


def merge_coll_stats(docs: list[dict]) -> dict:
    """Sum per-shard $collStats.storageStats into one row."""
    out = {"count": 0, "size": 0, "storage_size": 0, "total_index_size": 0, "nindexes": 0,
           "index_sizes": {}, "capped": False, "shards": []}
    for d in docs:
        s = d.get("storageStats") or {}
        out["count"] += s.get("count", 0) or 0
        out["size"] += s.get("size", 0) or 0
        out["storage_size"] += s.get("storageSize", 0) or 0
        out["total_index_size"] += s.get("totalIndexSize", 0) or 0
        out["nindexes"] = max(out["nindexes"], s.get("nindexes", 0) or 0)
        out["capped"] = out["capped"] or bool(s.get("capped"))
        for name, sz in (s.get("indexSizes") or {}).items():
            out["index_sizes"][name] = out["index_sizes"].get(name, 0) + (sz or 0)
        if d.get("shard"):
            out["shards"].append(d["shard"])
    out["avg_obj_size"] = (out["size"] / out["count"]) if out["count"] else 0
    return out


def merge_index_stats(docs: list[dict], now: datetime | None = None) -> list[dict]:
    """Sum $indexStats accesses per index name (one doc per shard via mongos)
    and keep the OLDEST 'since' — the window during which 0 ops really means 0."""
    now = now or datetime.now(timezone.utc)
    by_name: dict[str, dict] = {}
    for d in docs:
        name = d.get("name")
        acc = d.get("accesses") or {}
        since = acc.get("since")
        if since is not None and since.tzinfo is None:
            since = since.replace(tzinfo=timezone.utc)
        row = by_name.setdefault(name, {"name": name, "key": d.get("key") or {}, "ops": 0, "since": since,
                                        "spec": d.get("spec") or {}, "building": bool(d.get("building"))})
        row["ops"] += int(acc.get("ops", 0) or 0)
        if since and (row["since"] is None or since < row["since"]):
            row["since"] = since
    out = []
    for row in by_name.values():
        since = row["since"]
        row["tracked_hours"] = round((now - since).total_seconds() / 3600, 1) if since else None
        row["since"] = since.isoformat(sep=" ", timespec="seconds") if since else ""
        out.append(row)
    return out


def _index_flags(spec: dict) -> str:
    flags = []
    if spec.get("unique"):
        flags.append("unique")
    if spec.get("sparse"):
        flags.append("sparse")
    if "partialFilterExpression" in spec:
        flags.append("partial")
    if "expireAfterSeconds" in spec:
        flags.append(f"TTL {spec['expireAfterSeconds']}s")
    if spec.get("hidden"):
        flags.append("hidden")
    return ",".join(flags)


class ObjectsCollector(BaseCollector):
    def collect_sync(self) -> None:
        try:
            listing = self.conn.command("admin", {"listDatabases": 1})
        except Exception as exc:
            self.unavailable("database", f"listDatabases falhou: {short_error(exc)}")
            self.unavailable("indexes", f"listDatabases falhou: {short_error(exc)}")
            raise
        dbs = sorted(listing.get("databases") or [], key=lambda d: -(d.get("sizeOnDisk") or 0))

        db_rows: list[dict] = []
        coll_rows: list[dict] = []
        idx_rows: list[dict] = []
        budget = MAX_COLLECTIONS
        truncated = False
        idx_error = ""
        for d in dbs:
            name = d["name"]
            row = {"name": name, "size_on_disk": d.get("sizeOnDisk"), "empty": d.get("empty", False),
                   "collections": None, "views": None, "objects": None, "data_size": None,
                   "storage_size": None, "indexes": None, "index_size": None, "avg_obj_size": None,
                   "error": ""}
            try:
                st = self.conn.command(name, {"dbStats": 1, "scale": 1})
                row.update({
                    "collections": st.get("collections"), "views": st.get("views"),
                    "objects": st.get("objects"), "data_size": st.get("dataSize"),
                    "storage_size": st.get("storageSize"), "indexes": st.get("indexes"),
                    "index_size": st.get("indexSize"), "avg_obj_size": st.get("avgObjSize"),
                })
            except Exception as exc:
                row["error"] = short_error(exc)
            db_rows.append(row)

            if name in ("local",):
                continue  # oplog has its own panel; local.* is replication internals
            try:
                colls = self.conn.command(name, {"listCollections": 1, "filter": {"type": "collection"},
                                                 "nameOnly": True, "authorizedCollections": True})
                names = sorted(c["name"] for c in (colls.get("cursor") or {}).get("firstBatch", []))
            except Exception as exc:
                row["error"] = row["error"] or short_error(exc)
                continue
            for cname in names:
                if cname.startswith("system.") and name not in ("config",):
                    continue
                if budget <= 0:
                    truncated = True
                    break
                budget -= 1
                crow = {"db": name, "name": cname, "ns": f"{name}.{cname}", "error": ""}
                try:
                    docs = self.conn.aggregate(name, [{"$collStats": {"storageStats": {}}}, {"$project": {
                        "shard": 1, "storageStats.count": 1, "storageStats.size": 1,
                        "storageStats.storageSize": 1, "storageStats.totalIndexSize": 1,
                        "storageStats.nindexes": 1, "storageStats.indexSizes": 1, "storageStats.capped": 1}}],
                        collection=cname)
                    crow.update(merge_coll_stats(docs))
                except Exception as exc:
                    crow["error"] = short_error(exc)
                coll_rows.append(crow)

                if name in SYSTEM_DBS or not self.caps.can_index_stats:
                    continue
                try:
                    idocs = self.conn.aggregate(name, [{"$indexStats": {}}], collection=cname)
                    for ix in merge_index_stats(idocs):
                        ix.update({
                            "db": name, "collection": cname, "ns": crow["ns"],
                            "size": (crow.get("index_sizes") or {}).get(ix["name"]),
                            "flags": _index_flags(ix.pop("spec", {})),
                            "unused": ix["ops"] == 0 and ix["name"] != "_id_",
                        })
                        idx_rows.append(ix)
                except Exception as exc:
                    idx_error = short_error(exc)
            if truncated:
                break

        ttl = self.ttl
        self.available("database")
        self.cache.set("database.list", db_rows, ttl=ttl)
        self.cache.set("database.collections", sorted(coll_rows, key=lambda c: -(c.get("storage_size") or 0)), ttl=ttl)
        self.cache.set("database.truncated", truncated, ttl=ttl)
        if not self.caps.can_index_stats:
            self.unavailable("indexes", f"$indexStats requer MongoDB 3.2+ (servidor {self.caps.version}).")
        elif idx_error and not idx_rows:
            self.unavailable("indexes", f"$indexStats falhou: {idx_error}")
        else:
            self.available("indexes")
            self.cache.set("indexes.list", idx_rows, ttl=ttl)
            self.cache.set("indexes.unused", [i for i in idx_rows if i["unused"]], ttl=ttl)
