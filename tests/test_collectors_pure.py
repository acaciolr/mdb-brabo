"""Pure transforms used by collectors, fed with fabricated server replies."""
from datetime import datetime, timedelta, timezone

from bson import Timestamp

from mongodb_brabo.collectors.objects import merge_coll_stats, merge_index_stats
from mongodb_brabo.collectors.replication import summarize_oplog, summarize_rs_status
from mongodb_brabo.collectors.server_status import summarize
from mongodb_brabo.collectors.sessions import count_ops, normalize
from mongodb_brabo.collectors.sharding import summarize_chunks


def _ss(ops=0, wt_used=50, tickets_new=True):
    ss = {"connections": {"current": 10, "available": 90},
          "opcounters": {"insert": ops, "query": ops, "update": 0, "delete": 0, "getmore": 0, "command": ops},
          "network": {"bytesIn": ops * 100, "bytesOut": ops * 200, "numRequests": ops},
          "globalLock": {"currentQueue": {"total": 3, "readers": 1, "writers": 2}},
          "locks": {"Global": {"acquireCount": {"r": 10, "w": 5}, "acquireWaitCount": {"w": 1},
                               "timeAcquiringMicros": {"w": 500}}},
          "wiredTiger": {"cache": {"bytes currently in the cache": wt_used, "maximum bytes configured": 100,
                                   "tracked dirty bytes in the cache": 5}}}
    if tickets_new:
        ss["queues"] = {"execution": {"read": {"out": 1, "available": 127, "totalTickets": 128},
                                      "write": {"out": 0, "available": 128, "totalTickets": 128}}}
    else:
        ss["wiredTiger"]["concurrentTransactions"] = {"read": {"out": 2, "available": 126, "totalTickets": 128},
                                                      "write": {"out": 0, "available": 128, "totalTickets": 128}}
    return ss


def test_server_status_first_sample_has_no_rates():
    s = summarize(_ss(100), None, None)
    assert s["rates"]["total"] is None
    assert s["connections"]["max"] == 100 and s["connections"]["used_pct"] == 10.0
    assert s["wt_cache"]["used_pct"] == 50.0 and s["wt_cache"]["dirty_pct"] == 5.0
    assert s["queue"]["total"] == 3
    assert {r["mode"] for r in s["lock_stats"]} == {"IS", "IX"}


def test_server_status_rates_and_ticket_sources():
    s = summarize(_ss(300), _ss(100), 2.0)
    assert s["rates"]["insert"] == 100.0 and s["rates"]["total"] == 300.0
    assert s["rates"]["net_in"] == 10000.0
    assert s["tickets"]["source"] == "queues.execution"
    old = summarize(_ss(1, tickets_new=False), None, None)
    assert old["tickets"]["source"] == "wiredTiger.concurrentTransactions"
    assert old["tickets"]["read"]["out"] == 2


def test_sessions_internal_flagging():
    user = normalize({"opid": 1, "active": True, "op": "query", "ns": "a.b", "secs_running": 120,
                      "client": "10.0.0.1:5000", "command": {"find": "b", "filter": {"x": {"$in": [1, 2]}}}})
    hello = normalize({"opid": 2, "active": True, "op": "command", "ns": "admin.$cmd", "secs_running": 9,
                       "client": "10.0.0.2:1", "command": {"hello": 1, "maxAwaitTimeMS": 10000}})
    oplog = normalize({"opid": 3, "active": True, "op": "getmore", "ns": "local.oplog.rs", "secs_running": 999,
                       "client": "10.0.0.3:1", "command": {"getMore": 1}})
    thread = normalize({"opid": 4, "active": True, "op": "none", "desc": "TTLMonitor", "secs_running": 999})
    assert not user["internal"] and hello["internal"] and oplog["internal"] and thread["internal"]
    c = count_ops([user, hello, oplog, thread])
    assert c["long_running"] == 1 and c["longest_secs"] == 120 and c["user_active"] == 1


def test_rs_status_lag_vs_primary_and_no_primary():
    now = datetime(2026, 1, 1, tzinfo=timezone.utc)
    st = {"set": "rs0", "members": [
        {"_id": 0, "name": "a", "stateStr": "PRIMARY", "health": 1, "optimeDate": now},
        {"_id": 1, "name": "b", "stateStr": "SECONDARY", "health": 1, "optimeDate": now - timedelta(seconds=30)},
        {"_id": 2, "name": "c", "stateStr": "(not reachable/healthy)", "health": 0, "optimeDate": None},
        {"_id": 3, "name": "d", "stateStr": "ARBITER", "health": 1}]}
    s = summarize_rs_status(st)
    assert s["primary"] == "a" and s["max_lag_seconds"] == 30 and s["unhealthy"] == ["c"]
    st["members"][0]["stateStr"] = "SECONDARY"
    s2 = summarize_rs_status(st)
    assert s2["primary"] is None and s2["lag_reference"] == "membro mais recente"


def test_oplog_window_and_projection():
    o = summarize_oplog(Timestamp(1_000_000, 1), Timestamp(1_000_000 + 7200, 5), {"size": 100, "maxSize": 1000})
    assert o["window_hours"] == 2.0 and o["used_pct"] == 10.0
    assert o["avg_bytes_per_hour"] == 50.0 and o["projected_window_hours"] == 20.0
    empty = summarize_oplog(None, None, {})
    assert empty["window_hours"] is None


def test_merge_coll_and_index_stats_across_shards():
    m = merge_coll_stats([{"shard": "s1", "storageStats": {"count": 10, "size": 1000, "indexSizes": {"_id_": 5}}},
                          {"shard": "s2", "storageStats": {"count": 30, "size": 3000, "indexSizes": {"_id_": 7}}}])
    assert m["count"] == 40 and m["index_sizes"]["_id_"] == 12 and m["avg_obj_size"] == 100
    now = datetime(2026, 1, 10, tzinfo=timezone.utc)
    ix = merge_index_stats([{"name": "x_1", "accesses": {"ops": 0, "since": now - timedelta(days=2)}},
                            {"name": "x_1", "accesses": {"ops": 0, "since": now - timedelta(days=9)}},
                            {"name": "y_1", "accesses": {"ops": 4, "since": now}}], now=now)
    by = {i["name"]: i for i in ix}
    assert by["x_1"]["ops"] == 0 and by["x_1"]["tracked_hours"] == 216.0
    assert by["y_1"]["ops"] == 4


def test_chunks_by_uuid_and_ns():
    colls = [{"_id": "app.users", "uuid": "U1", "key": {"_id": "hashed"}}, {"_id": "old.c", "key": {"k": 1}}]
    groups = [{"_id": {"uuid": "U1", "shard": "s1"}, "n": 10}, {"_id": {"uuid": "U1", "shard": "s2"}, "n": 2},
              {"_id": {"ns": "old.c", "shard": "s1"}, "n": 4}]
    rows = {r["ns"]: r for r in summarize_chunks(groups, colls)}
    assert rows["app.users"]["spread"] == 8 and rows["app.users"]["total"] == 12
    assert rows["old.c"]["total"] == 4


def test_unreachable_member_epoch_optime_is_unknown_lag():
    """Regression (found on the real lab rs0): a DOWN member reports
    optimeDate=1970-01-01; that must be 'unknown lag', not 56 years."""
    now = datetime(2026, 1, 1, tzinfo=timezone.utc)
    st = {"set": "rs0", "members": [
        {"_id": 0, "name": "a", "stateStr": "PRIMARY", "health": 1, "optimeDate": now},
        {"_id": 1, "name": "b", "stateStr": "(not reachable/healthy)", "health": 0,
         "optimeDate": datetime(1970, 1, 1)}]}
    s = summarize_rs_status(st)
    assert s["max_lag_seconds"] == 0
    assert s["members"][1]["lag_seconds"] is None and s["members"][1]["optime"] == ""
    assert s["unhealthy"] == ["b"]
