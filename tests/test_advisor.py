from mongodb_brabo.advisors.engine import AdvisorEngine, Severity, summarize
from mongodb_brabo.core.cache import MetricsCache
from mongodb_brabo.core.capabilities import Capabilities


def _eval(cache):
    return AdvisorEngine(cache, Capabilities()).evaluate()


def _ids(findings):
    return {f.id: f for f in findings}


def healthy_cache() -> MetricsCache:
    c = MetricsCache()
    c.set("health.connections", {"current": 50, "available": 950, "max": 1000})
    c.set("wt.cache", {"used_pct": 70.0, "dirty_pct": 2.0})
    c.set("wt.tickets", {"read": {"out": 2, "available": 126, "total": 128},
                         "write": {"out": 1, "available": 127, "total": 128}})
    c.set("replication.status", {"set": "rs0", "primary": "a:27017", "max_lag_seconds": 1, "unhealthy": [],
                                 "members": [], "lag_reference": "PRIMARY"})
    c.set("oplog.info", {"window_hours": 72.0, "used_pct": 99.0, "projected_window_hours": 72.0, "max_bytes": 1})
    c.set("sessions.counts", {"long_running": 0, "waiting_lock": 0})
    c.set("locks.queue", {"total": 0})
    c.set("indexes.unused", [])
    c.set("profiler.levels", {"app": {"level": 1}})
    c.set("security.options", {"known": True, "auth_enabled": True, "authorization": "enabled", "tls_mode": "requireTLS"})
    return c


def test_healthy_has_no_findings():
    assert _eval(healthy_cache()) == []


def test_empty_cache_never_guesses():
    assert _eval(MetricsCache()) == []


def test_connections_near_limit():
    c = healthy_cache()
    c.set("health.connections", {"current": 850, "available": 150, "max": 1000})
    assert _ids(_eval(c))["MDB-CONN-001"].severity == Severity.WARNING
    c.set("health.connections", {"current": 950, "available": 50, "max": 1000})
    assert _ids(_eval(c))["MDB-CONN-001"].severity == Severity.CRITICAL


def test_wt_cache_and_dirty():
    c = healthy_cache()
    c.set("wt.cache", {"used_pct": 96.5, "dirty_pct": 25.0})
    f = _ids(_eval(c))
    assert f["MDB-WT-001"].severity == Severity.CRITICAL
    assert f["MDB-WT-002"].severity == Severity.CRITICAL
    assert "96.5" in f["MDB-WT-001"].evidence


def test_replication_lag_and_unhealthy_and_no_primary():
    c = healthy_cache()
    c.set("replication.status", {"set": "rs0", "primary": None, "max_lag_seconds": 120, "unhealthy": ["b:27017"],
                                 "lag_reference": "membro mais recente",
                                 "members": [{"name": "c:27017", "lag_seconds": 120}]})
    f = _ids(_eval(c))
    assert f["MDB-REPL-001"].severity == Severity.CRITICAL
    assert "c:27017" in f["MDB-REPL-001"].evidence
    assert f["MDB-REPL-002"].severity == Severity.CRITICAL
    assert f["MDB-REPL-003"].severity == Severity.CRITICAL


def test_oplog_window_full_vs_projected():
    c = healthy_cache()
    c.set("oplog.info", {"window_hours": 10.0, "used_pct": 99.0, "projected_window_hours": 10.0, "max_bytes": 1})
    assert _ids(_eval(c))["MDB-OPLOG-001"].severity == Severity.WARNING
    # Young oplog (not full): short current window but big projected window → no alarm.
    c.set("oplog.info", {"window_hours": 0.5, "used_pct": 3.0, "projected_window_hours": 300.0, "max_bytes": 1})
    assert "MDB-OPLOG-001" not in _ids(_eval(c))


def test_long_ops_and_lock_queue():
    c = healthy_cache()
    c.set("sessions.counts", {"long_running": 2, "longest_secs": 400, "waiting_lock": 3})
    f = _ids(_eval(c))
    assert f["MDB-OPS-001"].severity == Severity.WARNING
    assert f["MDB-LOCK-001"].severity == Severity.WARNING


def test_unused_index_trust_window():
    c = healthy_cache()
    c.set("indexes.unused", [{"ns": "a.b", "name": "x_1", "tracked_hours": 5}])
    assert _ids(_eval(c))["MDB-IDX-001"].severity == Severity.INFO
    c.set("indexes.unused", [{"ns": "a.b", "name": "x_1", "tracked_hours": 24 * 8}])
    assert _ids(_eval(c))["MDB-IDX-001"].severity == Severity.WARNING


def test_profiler_off_and_auth_off():
    c = healthy_cache()
    c.set("profiler.levels", {"admin": {"level": 0}, "app": {"level": 0}})
    c.set("security.options", {"known": True, "auth_enabled": False, "authorization": "disabled", "tls_mode": "disabled"})
    f = _ids(_eval(c))
    assert f["MDB-PROF-001"].severity == Severity.INFO
    assert f["MDB-SEC-001"].severity == Severity.CRITICAL
    assert f["MDB-SEC-002"].severity == Severity.INFO


def test_unknown_security_options_do_not_fire():
    c = healthy_cache()
    c.set("security.options", {"known": False})
    assert "MDB-SEC-001" not in _ids(_eval(c))


def test_balancer_stopped_with_imbalance():
    c = healthy_cache()
    c.set("sharding.balancer", {"mode": "off"})
    c.set("sharding.collections", [{"ns": "a.b", "spread": 20, "max": 30, "min": 10}])
    assert _ids(_eval(c))["MDB-SHARD-001"].severity == Severity.WARNING
    c.set("sharding.balancer", {"mode": "full"})
    assert "MDB-SHARD-001" not in _ids(_eval(c))


def test_sorted_and_score():
    c = healthy_cache()
    c.set("security.options", {"known": True, "auth_enabled": False, "tls_mode": "disabled"})
    c.set("sessions.counts", {"long_running": 1, "longest_secs": 70, "waiting_lock": 0})
    findings = _eval(c)
    sevs = [f.severity for f in findings]
    assert sevs == sorted(sevs, key=lambda s: ["CRITICAL", "WARNING", "INFO"].index(s.value))
    s = summarize(findings)
    assert s["critical"] == 1 and s["warning"] == 1 and s["info"] == 1
    assert s["score"] == 100 - 15 - 5 - 1
