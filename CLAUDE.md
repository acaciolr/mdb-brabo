# MDB BRABO — briefing for Claude Code sessions

MongoDB DBA TUI cockpit (Textual + rich + pymongo), sibling of ORA/SQL/PG BRABO.
Author: Acácio Lima Rocha (DBA BRABO). Used as the MONITORING option of the
MONGO SUPER DBA teaching platform — **the CLI flags are a contract with that
menu** (`--uri`, `--host/--port/--user/--password/--auth-db/--direct`,
`--label`, `--dba`, `--refresh`, `--start-saved all`, `--demo`,
`--report health --report-format json|text --report-output`, exit 0/1/2/3,
env `MDB_BRABO_PASSWORD`). Don't rename them.

## Hard rules (rationale in docs/ARCHITECTURE.md)
1. Collectors only run read commands. Admin commands only via
   `ConnectionManager.execute_action()` (refuses READ_ONLY), reached only via
   `BasePanel.request_admin_action()` (check_action → ConfirmModal).
2. Panels read only `MetricsCache`. Invented data exists only in
   `core/demo_data.py` (`--demo`, banner "DEMO — DADOS SIMULADOS").
3. Gate on `Capabilities` flags; unsupported → `collector.unavailable(ns, reason)`
   → panel shows NOT AVAILABLE + reason.
4. Collectors implement `collect_sync()` (pymongo is sync); never block the
   event loop. Every query bounded (client timeouts + maxTimeMS).
5. One collector failing never stops another (Scheduler isolates).
6. UI text in Portuguese, code identifiers in English.

## Layout
```
app.py                         CLI (argparse) + MdbBraboApp (tabs, F-keys)
mongodb_brabo/
  mdb_brabo.tcss               theme
  core/  config.py (AppConfig, URI redaction) · connection_manager.py (MongoClient, run(), execute_action)
         capabilities.py (parse_capabilities pure + discover) · permissions.py (check_action)
         cache.py · scheduler.py · connection_session.py (per tab; history loop)
         connections_store.py (~/.mdb_brabo/connections.json, no passwords) · history_store.py (SQLite)
         demo_data.py · paths.py (MDB_BRABO_HOME override) · version.py
  collectors/ server_status (health.* locks.* wt.*) · sessions ($currentOp) · profiler (system.profile)
              objects (database.* indexes.*) · replication (replication.* oplog.*) · sharding · security
  advisors/engine.py           rules MDB-*-NNN, thresholds as module constants
  reports/generator.py         --report health (text/json), exit codes
  widgets/ panels.py (Base, F1-F4, F12) · panels_data.py (F5 F6 F9 F10) · panels_cluster.py (F7 F8 F11)
           panels_history.py (h) · connection_pane.py · modals/help/text view · charts.py
tests/                         pytest; conftest isolates MDB_BRABO_HOME
```

## Dev
`./install.sh` → `.venv`; `.venv/bin/python -m pytest -q`; `.venv/bin/mdb-brabo --demo`.
