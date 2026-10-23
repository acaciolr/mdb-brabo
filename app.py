"""
MDB BRABO — MongoDB DBA Cockpit
================================
A terminal (TUI) MongoDB monitoring & administration tool, sibling of
ORA BRABO / SQL BRABO / PG BRABO. Built with Textual.
"The MongoDB DBA cockpit you access by SSH."
"""

from __future__ import annotations

import argparse
import asyncio
import logging
import os
import sys
from pathlib import Path

from mongodb_brabo.core.config import AppConfig
from mongodb_brabo.core.paths import brabo_home
from mongodb_brabo.core.version import BANNER_ART, __version__

log = logging.getLogger("mdb_brabo")


def _setup_logging() -> None:
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
        handlers=[logging.FileHandler(brabo_home() / "mdb_brabo.log")],
    )
    # pymongo's own topology chatter would drown our log.
    logging.getLogger("pymongo").setLevel(logging.WARNING)


# Textual is imported lazily so `--report`/`--version` work (and start fast)
# even on a box where only the CLI path is needed.
def _build_app_class():
    from textual.app import App, ComposeResult
    from textual.binding import Binding
    from textual.containers import Vertical
    from textual.widgets import ContentSwitcher, Footer, Header, Tab, Tabs

    from mongodb_brabo.core.connection_session import ConnectionSession
    from mongodb_brabo.widgets.add_connection_modal import AddConnectionModal
    from mongodb_brabo.widgets.connection_pane import ConnectionPane

    class MdbBraboApp(App):
        """Each MongoDB connection lives in its own tab (ConnectionPane)."""

        CSS_PATH = Path(__file__).parent / "mongodb_brabo" / "mdb_brabo.tcss"
        TITLE = f"MDB BRABO — MongoDB DBA Cockpit v{__version__}"
        SUB_TITLE = "The MongoDB DBA cockpit you access by SSH"

        BINDINGS = [
            Binding("f1", "show_panel('dashboard')", "Dashboard"),
            Binding("f2", "show_panel('sessions')", "Sessões"),
            Binding("f3", "show_panel('slowops')", "Top ops"),
            Binding("f4", "show_panel('locks')", "Locks"),
            Binding("f5", "show_panel('databases')", "Bancos"),
            Binding("f6", "show_panel('indexes')", "Índices"),
            Binding("f7", "show_panel('replication')", "Replica"),
            Binding("f8", "show_panel('sharding')", "Sharding"),
            Binding("f9", "show_panel('wiredtiger')", "WiredTiger"),
            Binding("f10", "show_panel('oplog')", "Oplog"),
            Binding("f11", "show_panel('security')", "Segurança"),
            Binding("f12", "show_panel('advisor')", "Advisor"),
            Binding("h", "show_panel('history')", "Histórico"),
            Binding("plus", "new_connection", "Nova conn"),
            Binding("ctrl+n", "new_connection", "Nova conn", show=False),
            Binding("ctrl+w", "close_tab", "Fechar aba"),
            Binding("question_mark", "help", "Ajuda"),
            Binding("q", "quit", "Sair"),
        ]

        def __init__(self, initial_configs: list[AppConfig] | None = None) -> None:
            super().__init__()
            self._initial = initial_configs or []
            self._sessions: dict[str, ConnectionSession] = {}
            self._active_id: str | None = None

        def compose(self) -> ComposeResult:
            yield Header(show_clock=True)
            with Vertical(id="main-vertical"):
                yield Tabs(id="connection-tabs")
                yield ContentSwitcher(id="pane-switcher")
            yield Footer()

        async def on_mount(self) -> None:
            log.info("MDB BRABO v%s starting", __version__)
            self.set_interval(0.5, self._tick)
            if self._initial:
                for cfg in self._initial:
                    await self.add_connection_tab(cfg)
            else:
                self.call_after_refresh(self.action_new_connection)

        async def on_unmount(self) -> None:
            for s in list(self._sessions.values()):
                await s.close()

        def on_tabs_tab_activated(self, event: Tabs.TabActivated) -> None:
            if event.tab is None:
                return
            sid = event.tab.id.removeprefix("tab-")
            self._active_id = sid
            try:
                self.query_one(ContentSwitcher).current = f"pane-{sid}"
            except Exception as exc:
                log.warning("switch error: %s", exc)

        def action_show_panel(self, panel: str) -> None:
            pane = self._active_pane()
            if pane:
                pane.show_panel(panel)

        def action_new_connection(self) -> None:
            def done(cfg: AppConfig | None) -> None:
                if cfg:
                    asyncio.create_task(self.add_connection_tab(cfg))
            self.push_screen(AddConnectionModal(), done)

        async def action_close_tab(self) -> None:
            if not self._active_id:
                return
            sid = self._active_id
            sess = self._sessions.pop(sid, None)
            if sess:
                await sess.close()
            self.query_one(Tabs).remove_tab(f"tab-{sid}")
            try:
                await self.query_one(f"#pane-{sid}").remove()
            except Exception:
                pass
            if not self._sessions:
                self._active_id = None
                self.set_timer(0.1, lambda: None if self._sessions else self.action_new_connection())

        def action_help(self) -> None:
            from mongodb_brabo.widgets.help_screen import HelpScreen
            self.push_screen(HelpScreen())

        async def add_connection_tab(self, cfg: AppConfig) -> None:
            session = ConnectionSession(cfg)
            self.notify(f"Conectando em {cfg.display_target() if not cfg.demo else 'DEMO'}…")
            try:
                await session.connect()
            except Exception as exc:
                msg = str(exc).split(", full error")[0]
                if "Authentication failed" in msg:
                    msg = "Falha de autenticação — confira usuário/senha/authSource."
                elif "ServerSelectionTimeoutError" in type(exc).__name__ or "timed out" in msg:
                    msg = f"Servidor inacessível: {msg[:200]}"
                log.error("Connection failed: %s", msg)
                self.notify(f"Conexão falhou: {msg}", severity="error", timeout=15)
                return
            self._sessions[session.id] = session
            await self.query_one(ContentSwitcher).mount(ConnectionPane(session, id=f"pane-{session.id}"))
            self.query_one(Tabs).add_tab(Tab(session.label, id=f"tab-{session.id}"))
            self.notify(f"Conectado: {session.label}")

        async def _tick(self) -> None:
            pane = self._active_pane()
            if pane:
                await pane.refresh_active_panel()
            for sid, s in self._sessions.items():
                try:
                    tab = self.query_one(f"#tab-{sid}", Tab)
                    label = s.label if s.is_healthy else f"⚠ {s.label}"
                    if str(tab.label) != label:
                        tab.label = label
                except Exception:
                    pass
            self._subtitle()

        def _subtitle(self) -> None:
            sess = self._sessions.get(self._active_id or "")
            pane = self._active_pane()
            if not sess or not pane:
                return
            key = pane.active_primary_key()
            age = sess.cache.age(key)
            if sess.cache.is_fresh(key):
                self.sub_title = sess.label
            elif age is None:
                self.sub_title = f"{sess.label} — aguardando coleta…"
            else:
                self.sub_title = f"{sess.label} — ⚠ dados velhos ({age:.0f}s)"

        def _active_pane(self):
            if not self._active_id:
                return None
            try:
                return self.query_one(f"#pane-{self._active_id}", ConnectionPane)
            except Exception:
                return None

        def session_for_tests(self):
            return self._sessions.get(self._active_id or "")

    return MdbBraboApp


def MdbBraboApp(initial_configs: list[AppConfig] | None = None):
    """Factory kept under the class name so callers/tests read naturally."""
    return _build_app_class()(initial_configs)


# ──────────────────────────────────────────────────────────────────────────
# CLI
# ──────────────────────────────────────────────────────────────────────────

def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        prog="mdb-brabo",
        description="MDB BRABO — MongoDB DBA TUI Cockpit (monitoramento e administração via SSH)",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=(
            "Exemplos:\n"
            "  mdb-brabo --uri 'mongodb://root_admin@192.168.60.1:27017/?authSource=admin&directConnection=true'\n"
            "  MDB_BRABO_PASSWORD=*** mdb-brabo --host 192.168.60.1 --user root_admin --auth-db admin --direct\n"
            "  mdb-brabo --start-saved all          # uma aba por perfil de ~/.mdb_brabo/connections.json\n"
            "  mdb-brabo --demo                     # sem banco, dados simulados marcados como DEMO\n"
            "  mdb-brabo --uri ... --report health --report-format json   # sem TUI, sai 0/1/2\n"
            "\nSenha: --password, ou (preferível, não aparece no ps) a variável MDB_BRABO_PASSWORD."
        ),
    )
    p.add_argument("--uri", default=None, help="Connection string mongodb:// ou mongodb+srv://")
    p.add_argument("--host", default=None, help="Host do mongod/mongos")
    p.add_argument("--port", default=27017, type=int)
    p.add_argument("--user", default=None)
    p.add_argument("--password", default=None, help="Senha (prefira MDB_BRABO_PASSWORD)")
    p.add_argument("--auth-db", default="admin", help="authSource (padrão: admin)")
    p.add_argument("--direct", action="store_true", help="directConnection=true (fala só com este nó)")
    p.add_argument("--tls", action="store_true", help="Conectar com TLS")
    p.add_argument("--label", default=None, help="Rótulo da aba")
    p.add_argument("--dba", action="store_true", help="Modo DBA (permite ações administrativas com confirmação)")
    p.add_argument("--refresh", default=5, type=int, help="Intervalo de coleta em segundos (padrão 5)")
    p.add_argument("--demo", action="store_true", help="Dados simulados, sem banco (marcados como DEMO)")
    p.add_argument("--start-saved", default=None, metavar="LABELS", help="Abrir perfis salvos: 'all' ou A,B,C")
    p.add_argument("--list-saved", action="store_true", help="Listar perfis salvos e sair")
    p.add_argument("--report", choices=["health"], default=None, help="Gera relatório e sai (sem TUI)")
    p.add_argument("--report-format", choices=["text", "json"], default="text")
    p.add_argument("--report-output", default=None, metavar="ARQUIVO")
    p.add_argument("--version", "-V", action="store_true", help="Mostra versão e sai")
    return p


def config_from_args(args) -> AppConfig | None:
    password = args.password if args.password is not None else os.environ.get("MDB_BRABO_PASSWORD")
    mode = "DBA" if args.dba else "READ_ONLY"
    if args.demo:
        return AppConfig(label=args.label or "DEMO — mdb-brabo@localhost", demo=True,
                         refresh_interval=args.refresh, mode=mode)
    if args.uri:
        cfg = AppConfig(uri=args.uri, user=args.user, password=password, label=args.label,
                        refresh_interval=args.refresh, mode=mode)
        # host/port only for display + history key; the URI is what connects.
        try:
            from pymongo.uri_parser import parse_uri
            nodes = parse_uri(args.uri, warn=True).get("nodelist") or []
            if nodes:
                cfg.host, cfg.port = nodes[0][0], int(nodes[0][1])
        except Exception:
            pass
        return cfg
    if args.host:
        return AppConfig(host=args.host, port=args.port, user=args.user, password=password,
                         auth_db=args.auth_db, direct=args.direct, tls=args.tls, label=args.label,
                         refresh_interval=args.refresh, mode=mode)
    return None


def _print_version() -> None:
    import platform
    from rich.console import Console
    try:
        import pymongo
        pmv = pymongo.version
    except Exception:
        pmv = "?"
    c = Console()
    c.print(BANNER_ART, style="bold #00ed64")
    c.print(f"  MDB BRABO — MongoDB DBA Cockpit   ·   v{__version__}", style="bold")
    c.print("  DBA BRABO · Acácio Lima Rocha", style="#8b949e")
    c.print(f"  Python {platform.python_version()}  ·  pymongo {pmv}", style="#484f58")


def _short_conn_error(exc: Exception) -> str:
    """pymongo appends the whole topology description to selection errors;
    the menu that calls --report only needs the first, human part."""
    msg = str(exc)
    for cut in (", full error", " (configured timeouts", ", Timeout:"):
        msg = msg.split(cut)[0]
    return msg[:300]


def _run_report(args) -> int:
    from mongodb_brabo.reports.generator import collect_report_data, exit_code, render_json, render_text
    cfg = config_from_args(args)
    if cfg is None:
        print("--report precisa de --uri, --host ou --demo.", file=sys.stderr)
        return 3
    try:
        data = collect_report_data(cfg)
    except Exception as exc:
        print(f"Falha ao gerar relatório: {_short_conn_error(exc)}", file=sys.stderr)
        return 3
    out = render_json(data) if args.report_format == "json" else render_text(data)
    if args.report_output:
        Path(args.report_output).write_text(out + "\n", encoding="utf-8")
        print(f"Relatório gravado em {args.report_output}")
    else:
        print(out)
    if not cfg.demo:
        # A cron'd report also feeds history.db — same metrics the TUI records.
        try:
            from mongodb_brabo.core.connection_session import history_store
            history_store().record(cfg.history_key(), {
                "connections_current": data["connections"].get("current"),
                "ops_per_sec": data["rates"].get("total"),
                "wt_cache_used_pct": data["wt_cache"].get("used_pct"),
                "resident_mb": data["memory"].get("resident_mb"),
                "data_size_bytes": sum((d.get("data_size") or 0) for d in data["databases"]) or None,
            })
        except Exception as exc:
            log.warning("history record from report failed: %s", exc)
    return exit_code(data)


def main(argv: list[str] | None = None) -> None:
    args = build_parser().parse_args(argv)
    if args.version:
        _print_version()
        sys.exit(0)
    _setup_logging()

    if args.report:
        sys.exit(_run_report(args))

    if args.list_saved:
        from mongodb_brabo.core.connections_store import load_connections, store_file
        conns = load_connections()
        if not conns:
            print(f"Nenhum perfil salvo ({store_file()}).")
        for c in conns:
            print(f"  {c.label:20s} [{c.mode:9s}] {c.user or '-'}@{c.host}:{c.port} authSource={c.auth_db}"
                  f"{' direct' if c.direct else ''}")
        sys.exit(0)

    configs: list[AppConfig] = []
    if args.start_saved and not args.demo:
        from mongodb_brabo.core.connections_store import load_connections, select
        chosen, missing = select(load_connections(), args.start_saved)
        if missing:
            print(f"Aviso: perfis não encontrados: {', '.join(missing)}", file=sys.stderr)
        configs = [c.to_app_config() for c in chosen]
        for c in configs:
            if args.dba:
                c.mode = "DBA"
            c.refresh_interval = args.refresh if args.refresh != 5 else c.refresh_interval
        if not configs:
            print("Nenhum perfil válido em --start-saved — abrindo o diálogo de conexão.", file=sys.stderr)
    else:
        cfg = config_from_args(args)
        if cfg:
            configs = [cfg]

    MdbBraboApp(configs).run()


if __name__ == "__main__":
    main()
