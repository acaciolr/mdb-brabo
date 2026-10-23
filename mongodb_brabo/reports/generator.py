"""
reports/generator.py
`mdb-brabo --report health` — one collection pass + Advisor, no TUI.
Usable from cron/CI: exit code 0 = no WARNING/CRITICAL, 1 = WARNING,
2 = CRITICAL, 3 = could not connect / generate (same convention as pg-brabo).

Uses exactly the same collectors as the TUI (their collect_sync()), so the
report can never disagree with the panels. Strictly read-only.

ServerStatus rates need two samples; a report takes two serverStatus
samples one second apart so ops/s are real numbers, not None.
"""

from __future__ import annotations

import json
import time

from mongodb_brabo.advisors.engine import AdvisorEngine, summarize
from mongodb_brabo.collectors.objects import ObjectsCollector
from mongodb_brabo.collectors.profiler import ProfilerCollector
from mongodb_brabo.collectors.replication import ReplicationCollector
from mongodb_brabo.collectors.security import SecurityCollector
from mongodb_brabo.collectors.server_status import ServerStatusCollector
from mongodb_brabo.collectors.sessions import SessionsCollector
from mongodb_brabo.collectors.sharding import ShardingCollector
from mongodb_brabo.core.cache import MetricsCache
from mongodb_brabo.core.capabilities import discover
from mongodb_brabo.core.config import AppConfig
from mongodb_brabo.core.connection_manager import ConnectionManager
from mongodb_brabo.core.version import __version__

PANEL_NAMESPACES = {
    "F1 Dashboard": "health", "F2 Sessões": "sessions", "F3 Top ops": "profiler",
    "F5 Bancos": "database", "F6 Índices": "indexes", "F7 Replica Set": "replication",
    "F8 Sharding": "sharding", "F9 WiredTiger": "wt", "F10 Oplog": "oplog", "F11 Segurança": "security",
}


def _collect_real(config: AppConfig, cache: MetricsCache):
    conn = ConnectionManager(config)
    conn.connect_sync()
    try:
        caps = discover(conn)
        errors: dict[str, str] = {}
        ss = ServerStatusCollector(conn, cache, caps)
        for i, cls in enumerate((None, SessionsCollector, ReplicationCollector, ProfilerCollector,
                                 ShardingCollector, SecurityCollector, ObjectsCollector)):
            c = ss if cls is None else cls(conn, cache, caps)
            try:
                c.collect_sync()
            except Exception as exc:
                errors[c.__class__.__name__] = str(exc)[:200]
            if cls is None:
                time.sleep(1.0)
                try:
                    ss.collect_sync()  # second sample → real rates
                except Exception:
                    pass
        return caps, errors
    finally:
        conn.close_sync()


def collect_report_data(config: AppConfig) -> dict:
    cache = MetricsCache()
    if config.demo:
        from mongodb_brabo.core.demo_data import DemoRunner
        runner = DemoRunner(cache)
        runner.tick()
        caps, errors = runner.capabilities, {}
    else:
        caps, errors = _collect_real(config, cache)

    findings = AdvisorEngine(cache, caps).evaluate()
    panels = {}
    for label, ns in PANEL_NAMESPACES.items():
        avail = cache.get(f"{ns}.available")
        panels[label] = "OK" if avail else ("NOT AVAILABLE: " + (cache.get(f"{ns}.reason") or "sem dados"))

    return {
        "tool": f"MDB BRABO {__version__}",
        "demo": bool(config.demo),
        "generated_at": time.strftime("%Y-%m-%d %H:%M:%S %z"),
        "target": "DEMO (dados simulados)" if config.demo else config.display_target(),
        "mode": config.mode,
        "capabilities": caps.summary(),
        "identity": cache.get("health.identity") or {},
        "connections": cache.get("health.connections") or {},
        "rates": cache.get("health.rates") or {},
        "memory": cache.get("health.memory") or {},
        "wt_cache": cache.get("wt.cache") or {},
        "tickets": cache.get("wt.tickets") or {},
        "queue": cache.get("locks.queue") or {},
        "sessions": cache.get("sessions.counts") or {},
        "replication": cache.get("replication.status"),
        "oplog": cache.get("oplog.info"),
        "databases": cache.get("database.list") or [],
        "unused_indexes": cache.get("indexes.unused") or [],
        "profiler_levels": cache.get("profiler.levels") or {},
        "security": cache.get("security.options") or {},
        "panels": panels,
        "collector_errors": errors,
        "summary": summarize(findings),
        "findings": [f.as_dict() for f in findings],
    }


def exit_code(data: dict) -> int:
    sev = {f["severity"] for f in data["findings"]}
    return 2 if "CRITICAL" in sev else (1 if "WARNING" in sev else 0)


def render_json(data: dict) -> str:
    return json.dumps(data, indent=2, ensure_ascii=False, default=str)


def _b(n) -> str:
    if n is None:
        return "—"
    n = float(n)
    for u in ("B", "KB", "MB", "GB", "TB"):
        if abs(n) < 1024:
            return f"{n:,.1f} {u}"
        n /= 1024
    return f"{n:,.1f} PB"


def render_text(data: dict) -> str:
    w = 78
    L = ["=" * w, "MDB BRABO — HEALTH REPORT".center(w), "=" * w]
    if data["demo"]:
        L.append("*** MODO DEMO — DADOS SIMULADOS, NÃO É UM SERVIDOR REAL ***".center(w))
    caps = data["capabilities"]
    ident = data["identity"]
    L += [f"Gerado em:   {data['generated_at']}",
          f"Alvo:        {data['target']}  [{data['mode']}]",
          f"Servidor:    MongoDB {caps.get('version')}  papel={caps.get('role')}  "
          f"engine={caps.get('storage_engine') or '—'}  host={ident.get('host', '—')}",
          f"Usuário:     {', '.join(caps.get('users') or []) or '(sem auth)'}  roles={', '.join(caps.get('roles') or []) or '—'}",
          ""]
    c, r, wt, q = data["connections"], data["rates"], data["wt_cache"], data["queue"]
    L.append("-- Métricas " + "-" * (w - 12))
    if c:
        L.append(f"Conexões:    {c.get('current')} atuais / {c.get('available')} disponíveis ({c.get('used_pct')}%)")
    if r.get("total") is not None:
        L.append(f"Operações/s: total={r['total']:.1f}  insert={r['insert']:.1f} query={r['query']:.1f} "
                 f"update={r['update']:.1f} delete={r['delete']:.1f} command={r['command']:.1f}")
    if wt:
        L.append(f"WT cache:    {_b(wt.get('bytes_in_cache'))} de {_b(wt.get('max_bytes'))} "
                 f"(usado {wt.get('used_pct')}%, dirty {wt.get('dirty_pct')}%)")
    if q:
        L.append(f"Fila locks:  total={q.get('total')} leitores={q.get('readers')} escritores={q.get('writers')}")
    s = data["sessions"]
    if s:
        L.append(f"Operações:   {s.get('total')} em curso, {s.get('user_active')} de usuário ativas, "
                 f"{s.get('long_running')} ≥60s, {s.get('waiting_lock')} esperando lock")
    if data.get("oplog"):
        o = data["oplog"]
        L.append(f"Oplog:       janela {o.get('window_hours')}h, usado {o.get('used_pct')}% de {_b(o.get('max_bytes'))}")
    if data["databases"]:
        L.append(f"Bancos:      " + ", ".join(f"{d['name']}={_b(d.get('size_on_disk'))}" for d in data["databases"][:8]))
    L += ["", "-- Painéis " + "-" * (w - 11)]
    for k, v in data["panels"].items():
        L.append(f"{k:16s} {v}")
    sm = data["summary"]
    L += ["", f"-- Advisor (score {sm['score']}/100 — CRITICAL {sm['critical']}  WARNING {sm['warning']}  "
              f"INFO {sm['info']}) " + "-" * 4]
    if not data["findings"]:
        L.append("Nenhum achado — todas as regras passaram.")
    for f in data["findings"]:
        L.append(f"[{f['severity']:8s}] {f['id']}  {f['title']}")
        L.append(f"           evidência: {f['evidence']}")
        L.append(f"           recomendação: {f['recommendation']}")
    if data["collector_errors"]:
        L += ["", "-- Erros de coleta " + "-" * (w - 19)]
        for k, v in data["collector_errors"].items():
            L.append(f"{k}: {v}")
    L.append("=" * w)
    return "\n".join(L)
