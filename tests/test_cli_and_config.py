import json
import os
import subprocess
import sys
from pathlib import Path

import pytest

import app
from mongodb_brabo.core.config import AppConfig, redact_uri
from mongodb_brabo.core.connection_manager import ConnectionManager, ReadOnlyViolation
from mongodb_brabo.core.permissions import check_action
from mongodb_brabo.core.capabilities import Capabilities

ROOT = Path(__file__).resolve().parents[1]


def _run(*args, env_extra=None):
    env = dict(os.environ)
    env.update(env_extra or {})
    return subprocess.run([sys.executable, str(ROOT / "app.py"), *args], capture_output=True, text=True,
                          env=env, cwd=ROOT, timeout=60)


def test_report_demo_json_exit_code_warning(tmp_path):
    r = _run("--demo", "--report", "health", "--report-format", "json")
    assert r.returncode == 1, r.stderr          # demo has a WARNING (lag 12s / long op)
    data = json.loads(r.stdout)
    assert data["demo"] is True
    ids = {f["id"] for f in data["findings"]}
    assert {"MDB-REPL-001", "MDB-OPS-001", "MDB-IDX-001", "MDB-SEC-002"} <= ids
    assert data["panels"]["F7 Replica Set"] == "OK"


def test_report_demo_text_to_file(tmp_path):
    out = tmp_path / "r.txt"
    r = _run("--demo", "--report", "health", "--report-output", str(out))
    assert r.returncode == 1
    txt = out.read_text()
    assert "MODO DEMO" in txt and "MDB BRABO — HEALTH REPORT" in txt


def test_report_unreachable_exit_3():
    r = _run("--host", "127.0.0.1", "--port", "1", "--report", "health")
    assert r.returncode == 3
    assert "Falha" in r.stderr


def test_report_without_target():
    assert _run("--report", "health").returncode == 3


def test_version_and_help():
    assert "MDB BRABO" in _run("--version").stdout
    h = _run("--help").stdout
    for flag in ("--uri", "--auth-db", "--direct", "--start-saved", "--report-format", "MDB_BRABO_PASSWORD"):
        assert flag in h


def test_password_from_env_and_uri_parsing(monkeypatch):
    monkeypatch.setenv("MDB_BRABO_PASSWORD", "s3cr3t")
    args = app.build_parser().parse_args(["--host", "h", "--user", "u", "--direct"])
    cfg = app.config_from_args(args)
    kw = cfg.client_kwargs()
    assert kw["password"] == "s3cr3t" and kw["username"] == "u" and kw["directConnection"] is True
    assert kw["authSource"] == "admin" and cfg.is_readonly
    args = app.build_parser().parse_args(["--uri", "mongodb://u@10.1.2.3:27018/?authSource=admin", "--dba"])
    cfg = app.config_from_args(args)
    assert (cfg.host, cfg.port) == ("10.1.2.3", 27018)
    assert cfg.client_kwargs()["password"] == "s3cr3t" and not cfg.is_readonly


def test_redact_uri():
    assert redact_uri("mongodb://root:pw@h:1/?authSource=admin") == "mongodb://root:****@h:1/?authSource=admin"
    assert redact_uri("mongodb://h:1/") == "mongodb://h:1/"


def test_readonly_gate_twice():
    cfg = AppConfig(mode="READ_ONLY")
    caps = Capabilities(all_privileges=True)
    assert not check_action("kill_op", cfg, caps).allowed
    cm = ConnectionManager(cfg)
    with pytest.raises(ReadOnlyViolation):
        cm.execute_action_sync("admin", {"killOp": 1, "op": 1})
    dba = AppConfig(mode="DBA")
    assert check_action("kill_op", dba, caps).allowed
    assert not check_action("step_down", dba, caps).allowed      # standalone: no stepDown
    assert not check_action("kill_op", AppConfig(mode="DBA", demo=True), caps).allowed


def test_saved_connections_never_store_password(monkeypatch):
    from mongodb_brabo.core import connections_store as cs
    monkeypatch.setattr(cs, "_HAS_KEYRING", False)
    cs.save_connection(cs.SavedConnection(label="NODE01", host="10.0.0.1", user="root_admin", direct=True), "pw")
    raw = cs.store_file().read_text()
    assert "pw" not in raw and "password" not in raw
    monkeypatch.setenv("MDB_BRABO_PASSWORD_NODE01", "fromenv")
    cfg = cs.load_connections()[0].to_app_config()
    assert cfg.password == "fromenv" and cfg.direct and cfg.user == "root_admin"
    chosen, missing = cs.select(cs.load_connections(), "node01,nope")
    assert len(chosen) == 1 and missing == ["nope"]
    r = _run("--list-saved", env_extra={"MDB_BRABO_HOME": str(cs.store_file().parent)})
    assert "NODE01" in r.stdout
