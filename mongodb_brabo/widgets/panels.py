"""
widgets/panels.py
Base panel + F1 Dashboard, F2 Sessões, F3 Top ops, F4 Locks, F12 Advisor.

Rules every panel follows:
  * read ONLY from MetricsCache — never call MongoDB directly;
  * when a namespace is marked unavailable, show NOT AVAILABLE + the reason
    the collector recorded (privilege, topology, version) — never blank,
    never invented numbers;
  * administrative actions go through BasePanel.request_admin_action(),
    the single UI entry point (permission check → confirmation → execute).
"""

from __future__ import annotations

import asyncio
import logging
import time

from rich.console import Group
from rich.panel import Panel
from rich.table import Table
from rich.text import Text
from textual.app import ComposeResult
from textual.binding import Binding
from textual.containers import Horizontal
from textual.widget import Widget
from textual.widgets import DataTable, Input, Static

from mongodb_brabo.core.connection_manager import ReadOnlyViolation
from mongodb_brabo.core.permissions import check_action
from mongodb_brabo.widgets.charts import Graph, fmt, fmt_bytes, fmt_duration, fmt_int, pct_bar
from mongodb_brabo.widgets.confirm_modal import ConfirmModal
from mongodb_brabo.widgets.text_view_screen import TextViewScreen

log = logging.getLogger(__name__)

SEV_STYLE = {"CRITICAL": "bold red", "WARNING": "bold yellow", "INFO": "cyan"}
SEV_ICON = {"CRITICAL": "✖", "WARNING": "▲", "INFO": "ℹ"}


def T(value, style: str = "") -> Text:
    """DataTable treats str cells as markup; MongoDB commands are full of
    '[' and '$' — wrap user data in Text so it's shown literally."""
    return Text("" if value is None else str(value), style=style)


def fill_table(dt: DataTable, columns: tuple[str, ...], rows: list[tuple]) -> None:
    if not dt.columns:
        dt.add_columns(*columns)
    cursor = dt.cursor_row
    dt.clear()
    for r in rows:
        dt.add_row(*r)
    if dt.row_count and cursor > 0:
        dt.move_cursor(row=min(cursor, dt.row_count - 1))


class BasePanel(Widget):
    REFRESH_RATE: float = 1.0
    TITLE = ""

    DEFAULT_CSS = """
    BasePanel { height: 100%; overflow-y: auto; padding: 0 1; }
    BasePanel DataTable { height: auto; max-height: 24; }
    .hidden { display: none; }
    """

    def __init__(self, session, **kwargs) -> None:
        super().__init__(**kwargs)
        self.session = session
        self.cache = session.cache
        self.config = session.config
        self._last_refresh = 0.0

    @property
    def caps(self):
        return self.session.capabilities

    async def tick(self) -> None:
        now = time.monotonic()
        if now - self._last_refresh >= self.REFRESH_RATE:
            self._last_refresh = now
            try:
                await self.refresh_data()
            except Exception as exc:  # a rendering bug must never kill the app
                log.warning("%s refresh error: %s", self.__class__.__name__, exc, exc_info=True)

    async def refresh_data(self) -> None:  # pragma: no cover - overridden
        pass

    # ── helpers ──────────────────────────────────────────────────────
    def banner(self) -> Text:
        t = Text()
        if self.cache.get("demo"):
            t.append(" DEMO — DADOS SIMULADOS ", style="bold black on yellow")
            t.append(" ")
        t.append(f" {self.TITLE} ", style="bold black on #00ed64")
        mode = "DBA" if not self.config.is_readonly else "READ ONLY"
        t.append(f"  {mode}", style="bold red" if mode == "DBA" else "bold cyan")
        return t

    def not_available(self, ns: str) -> Panel | None:
        avail = self.cache.get(f"{ns}.available")
        if avail is None:
            return Panel(Text("Aguardando primeira coleta…", style="dim"), border_style="dim")
        if avail is False:
            reason = self.cache.get(f"{ns}.reason") or "sem dados"
            return Panel(Text(reason, style="yellow"), title="[bold yellow]NOT AVAILABLE[/]",
                         border_style="yellow", padding=(1, 2))
        return None

    def request_admin_action(self, action: str, db: str, cmd: dict, title: str, body: str) -> None:
        """THE single UI path for administrative commands:
        check_action() → ConfirmModal → ConnectionManager.execute_action()
        (which itself refuses READ ONLY a second time)."""
        chk = check_action(action, self.config, self.caps)
        if not chk.allowed:
            self.app.notify(chk.reason, severity="error", timeout=6)
            return
        from bson import json_util
        cmd_txt = f"db.getSiblingDB('{db}').runCommand({json_util.dumps(cmd)})"

        async def _do() -> None:
            try:
                ok, msg = await self.session.conn_manager.execute_action(db, cmd)
            except ReadOnlyViolation as exc:
                ok, msg = False, str(exc)
            self.app.notify(f"{title}: {'OK — ' + msg if ok else 'FALHOU — ' + msg}",
                            severity="information" if ok else "error", timeout=8)

        def on_confirm(confirmed: bool | None) -> None:
            if confirmed:
                asyncio.create_task(_do())

        self.app.push_screen(ConfirmModal(title=title, body=body, command=cmd_txt), on_confirm)


# ---------------------------------------------------------------------------
# F1 DASHBOARD
# ---------------------------------------------------------------------------

class DashboardPanel(BasePanel):
    TITLE = "F1 DASHBOARD"
    DEFAULT_CSS = BasePanel.DEFAULT_CSS + """
    #dash-charts { height: 11; }
    #dash-charts Graph { width: 1fr; height: 11; margin: 0 1 0 0; }
    """

    def compose(self) -> ComposeResult:
        yield Static(id="dash-header")
        yield Static(id="dash-metrics")
        with Horizontal(id="dash-charts"):
            yield Graph("Conexões", color=(68, 180, 255), id="g-conn")
            yield Graph("Ops/s", color=(0, 237, 100), id="g-ops")
            yield Graph("WT cache %", color=(252, 213, 121), unit="%", id="g-wt")
            yield Graph("Rede KB/s", color=(191, 121, 252), id="g-net")
        yield Static(id="dash-advisor")

    async def refresh_data(self) -> None:
        na = self.not_available("health")
        ident = self.cache.get("health.identity") or {}
        caps = self.caps
        t = Table.grid(expand=True, padding=(0, 2))
        for _ in range(5):
            t.add_column(ratio=1)
        role = caps.role_label if caps.version else "?"
        role_style = "green" if "PRIMARY" in role or role in ("STANDALONE", "MONGOS") else "yellow"
        t.add_row(Text(ident.get("host") or self.config.display_target(), style="bold cyan"),
                  Text(f"MongoDB {caps.version or ident.get('version') or '?'}"),
                  Text(role, style=role_style),
                  Text(f"rs: {caps.set_name}" if caps.set_name else (ident.get("process") or "")),
                  Text(f"up {fmt_duration(ident.get('uptime_seconds'))}", style="bold"))
        t.add_row(Text(f"engine: {caps.storage_engine or ident.get('storage_engine') or '—'}", style="dim"),
                  Text(f"user: {', '.join(caps.users) or '(sem auth)'}", style="dim"),
                  Text(f"roles: {', '.join(caps.roles) or '—'}", style="dim"), Text(""), Text(""))
        self.query_one("#dash-header", Static).update(
            Panel(Group(self.banner(), t), title="[bold white] MDB BRABO [/]", border_style="green"))

        if na:
            self.query_one("#dash-metrics", Static).update(na)
        else:
            self.query_one("#dash-metrics", Static).update(self._metrics())
        self._graphs()
        self._advisor()

    def _metrics(self):
        c = self.cache.get("health.connections") or {}
        r = self.cache.get("health.rates") or {}
        mem = self.cache.get("health.memory") or {}
        wt = self.cache.get("wt.cache") or {}
        tk = self.cache.get("wt.tickets") or {}
        q = self.cache.get("locks.queue") or {}

        a = Table.grid(padding=(0, 1))
        a.add_column(width=14)
        a.add_column()
        a.add_row("[dim]Atuais[/]", f"[bold]{fmt_int(c.get('current'))}[/]")
        a.add_row("[dim]Disponíveis[/]", fmt_int(c.get("available")))
        a.add_row("[dim]Ativas[/]", fmt_int(c.get("active")))
        a.add_row("[dim]Uso[/]", pct_bar(c.get("used_pct") or 0, 12, 80, 90))
        a.add_row("[dim]Criadas[/]", fmt_int(c.get("total_created")))

        b = Table.grid(padding=(0, 1))
        b.add_column(width=10)
        b.add_column(justify="right")
        b.add_row("[dim]total[/]", f"[bold]{fmt(r.get('total'))}[/]")
        for k in ("insert", "query", "update", "delete", "getmore", "command"):
            b.add_row(f"[dim]{k}[/]", fmt(r.get(k)))

        m = Table.grid(padding=(0, 1))
        m.add_column(width=14)
        m.add_column()
        m.add_row("[dim]Resident[/]", f"{fmt_int(mem.get('resident_mb'))} MB")
        m.add_row("[dim]Virtual[/]", f"{fmt_int(mem.get('virtual_mb'))} MB")
        m.add_row("[dim]Rede in/out[/]", f"{fmt_bytes(r.get('net_in'))}/s · {fmt_bytes(r.get('net_out'))}/s")
        m.add_row("[dim]WT cache[/]", pct_bar(wt.get("used_pct") or 0, 12, 80, 95) if wt else Text("—"))
        m.add_row("[dim]WT dirty[/]", pct_bar(wt.get("dirty_pct") or 0, 12, 5, 20) if wt else Text("—"))
        rd, wr = tk.get("read") or {}, tk.get("write") or {}
        m.add_row("[dim]Tickets R/W[/]", f"{fmt_int(rd.get('out'))}/{fmt_int(rd.get('total'))} · "
                                         f"{fmt_int(wr.get('out'))}/{fmt_int(wr.get('total'))}")
        m.add_row("[dim]Fila lock[/]", f"{q.get('total', 0)} (R {q.get('readers', 0)} / W {q.get('writers', 0)})")

        outer = Table.grid(expand=True, padding=(0, 1))
        for _ in range(3):
            outer.add_column(ratio=1)
        outer.add_row(Panel(a, title="[bold green]Conexões[/]", border_style="green"),
                      Panel(b, title="[bold cyan]Operações/s[/]", border_style="cyan"),
                      Panel(m, title="[bold magenta]Memória · WiredTiger · Fila[/]", border_style="magenta"))
        return outer

    def _graphs(self) -> None:
        conns = [c.get("current", 0) for c in self.cache.get_history_values("health.connections") if isinstance(c, dict)]
        rates = [r for r in self.cache.get_history_values("health.rates") if isinstance(r, dict)]
        ops = [r.get("total") or 0 for r in rates if r.get("total") is not None]
        net = [((r.get("net_in") or 0) + (r.get("net_out") or 0)) / 1024 for r in rates if r.get("net_in") is not None]
        wt = [w.get("used_pct") or 0 for w in self.cache.get_history_values("wt.cache") if isinstance(w, dict)]
        self.query_one("#g-conn", Graph).update_data(conns)
        self.query_one("#g-ops", Graph).update_data(ops)
        self.query_one("#g-wt", Graph).update_data(wt)
        self.query_one("#g-net", Graph).update_data(net)

    def _advisor(self) -> None:
        s = self.cache.get("advisor.summary") or {}
        findings = self.cache.get("advisor.findings") or []
        score = s.get("score", 100)
        color = "green" if score >= 80 else ("yellow" if score >= 50 else "red")
        t = Text()
        t.append(f"  {'█' * int(score / 100 * 30)}{'░' * (30 - int(score / 100 * 30))} {score}%   ", style=color)
        t.append(f"CRITICAL {s.get('critical', 0)}  WARNING {s.get('warning', 0)}  INFO {s.get('info', 0)}\n",
                 style="bold")
        for f in findings[:5]:
            sev = f.get("severity", "INFO")
            t.append(f"  {SEV_ICON.get(sev, '•')} {f.get('title')}", style=SEV_STYLE.get(sev, ""))
            t.append(f"  — {f.get('evidence', '')}\n", style="dim")
        if not findings:
            t.append("  Nenhum achado — todas as regras passaram.\n", style="dim green")
        self.query_one("#dash-advisor", Static).update(
            Panel(t, title="[bold]Saúde[/] [dim](F12 = Advisor completo)[/]", border_style="blue"))


# ---------------------------------------------------------------------------
# F2 SESSÕES ($currentOp)
# ---------------------------------------------------------------------------

class SessionsPanel(BasePanel):
    TITLE = "F2 SESSÕES · $currentOp"
    BINDINGS = [
        Binding("d", "detail", "Detalhe"),
        Binding("k", "kill", "Kill (DBA)"),
        Binding("slash", "toggle_filter", "Filtro"),
        Binding("i", "toggle_internal", "Internas"),
    ]

    def __init__(self, *a, **kw) -> None:
        super().__init__(*a, **kw)
        self._show_internal = False
        self._visible: list[dict] = []

    def compose(self) -> ComposeResult:
        yield Static(id="sess-label")
        yield Input(placeholder="Filtro: ns / op / client / app / user / comando…", id="sess-filter", classes="hidden")
        yield Static(id="sess-na")
        yield DataTable(id="sess-table", cursor_type="row", zebra_stripes=True)

    async def refresh_data(self) -> None:
        na = self.not_available("sessions")
        self.query_one("#sess-na", Static).update(na or "")
        ops = self.cache.get("sessions.list") or []
        counts = self.cache.get("sessions.counts") or {}
        flt = self.query_one("#sess-filter", Input).value.lower().strip()
        if not self._show_internal:
            ops = [o for o in ops if not o.get("internal")]
        if flt:
            ops = [o for o in ops if any(flt in str(o.get(k) or "").lower()
                                         for k in ("ns", "op", "client", "app_name", "user", "command", "desc"))]
        self._visible = ops
        rows = []
        for o in ops:
            secs = o.get("secs_running") or 0
            style = "dim" if o.get("internal") else ("red" if secs >= 60 else ("yellow" if secs >= 5 else "green"))
            rows.append((T(o.get("opid")), T(o.get("op"), style), T(o.get("ns")), T(fmt_duration(secs), style),
                         T(o.get("client")), T(o.get("app_name")), T(o.get("user")), T(o.get("desc")),
                         T("SIM", "bold red") if o.get("waiting_for_lock") else T(""),
                         T(o.get("plan_summary")), T((o.get("command") or "")[:90])))
        fill_table(self.query_one("#sess-table", DataTable),
                   ("opid", "op", "ns", "secs_running", "client", "app", "user", "desc", "waitLock", "plano", "comando"),
                   rows)
        t = self.banner()
        t.append(f"   total {counts.get('total', 0)} · usuário ativas {counts.get('user_active', 0)} · "
                 f"≥60s {counts.get('long_running', 0)} · esperando lock {counts.get('waiting_lock', 0)}")
        t.append(f"   [internas {'visíveis' if self._show_internal else 'ocultas'}]  D=detalhe K=kill /=filtro I=internas",
                 style="dim")
        self.query_one("#sess-label", Static).update(t)

    def _selected(self) -> dict | None:
        dt = self.query_one("#sess-table", DataTable)
        if 0 <= dt.cursor_row < len(self._visible):
            return self._visible[dt.cursor_row]
        return None

    def action_toggle_filter(self) -> None:
        inp = self.query_one("#sess-filter", Input)
        if inp.has_class("hidden"):
            inp.remove_class("hidden")
            inp.focus()
        else:
            inp.add_class("hidden")
            inp.value = ""
            self.query_one("#sess-table").focus()

    def action_toggle_internal(self) -> None:
        self._show_internal = not self._show_internal
        self._last_refresh = 0

    def action_detail(self) -> None:
        o = self._selected()
        if o:
            self.app.push_screen(TextViewScreen(f"Operação opid {o.get('opid')}",
                                                "\n".join(f"{k}: {v}" for k, v in o.items())))

    def action_kill(self) -> None:
        o = self._selected()
        if not o:
            return
        opid = o.get("opid")
        self.request_admin_action(
            "kill_op", "admin", {"killOp": 1, "op": opid}, "killOp",
            f"Matar a operação {opid} ({o.get('op')} em {o.get('ns')}, {fmt_duration(o.get('secs_running'))}, "
            f"client {o.get('client')})?")


# ---------------------------------------------------------------------------
# F3 TOP OPERAÇÕES / CONSULTAS LENTAS
# ---------------------------------------------------------------------------

class SlowOpsPanel(BasePanel):
    TITLE = "F3 TOP OPERAÇÕES · system.profile"
    REFRESH_RATE = 2
    BINDINGS = [
        Binding("d", "detail", "Detalhe"),
        Binding("p", "profiler_on", "Profiler ON (DBA)"),
        Binding("o", "profiler_off", "Profiler OFF (DBA)"),
    ]

    def __init__(self, *a, **kw) -> None:
        super().__init__(*a, **kw)
        self._visible: list[dict] = []

    def compose(self) -> ComposeResult:
        yield Static(id="slow-label")
        yield Static(id="slow-help")
        yield DataTable(id="slow-table", cursor_type="row", zebra_stripes=True)

    async def refresh_data(self) -> None:
        levels = self.cache.get("profiler.levels") or {}
        top = self.cache.get("profiler.top") or []
        na = self.not_available("profiler")
        on = {db: v for db, v in levels.items() if (v.get("level") or 0) > 0}
        lbl = self.banner()
        lvl_txt = "  ".join(f"{db}={v.get('level')}({v.get('slowms')}ms)" for db, v in sorted(levels.items()))
        lbl.append(f"   profiler por banco: {lvl_txt or '—'}", style="dim")
        self.query_one("#slow-label", Static).update(lbl)

        if na is not None and self.cache.get("profiler.available") is False:
            self.query_one("#slow-help", Static).update(na)
        elif not on:
            self.query_one("#slow-help", Static).update(Panel(Text(
                "Profiler DESLIGADO em todos os bancos — não há histórico de consultas lentas.\n"
                "Para ligar (nível 1 = só ops acima de slowms):\n"
                "    use <banco>\n    db.setProfilingLevel(1, { slowms: 100 })\n"
                "ou, em modo --dba, tecle [P] aqui. Desligar: db.setProfilingLevel(0) / [O].\n"
                "Enquanto isso, abaixo: operações lentas EM EXECUÇÃO agora ($currentOp ≥ 1s).",
                style="yellow"), title="[bold yellow]PROFILER NOT CONFIGURED[/]", border_style="yellow"))
        else:
            self.query_one("#slow-help", Static).update("")

        if on and top:
            self._visible = top
            rows = [(T(r["ts"]), T(r["op"]), T(r["ns"]), T(fmt_int(r["millis"]), "bold red" if r["millis"] >= 1000 else "yellow"),
                     T(fmt_int(r.get("docs_examined"))), T(fmt_int(r.get("keys_examined"))),
                     T(fmt_int(r.get("nreturned"))), T(r.get("plan_summary"), "red" if "COLLSCAN" in (r.get("plan_summary") or "") else ""),
                     T(r.get("app_name") or r.get("client")), T((r.get("command") or "")[:80])) for r in top]
            cols = ("ts", "op", "ns", "millis", "docsExam", "keysExam", "nret", "plano", "app/client", "comando")
        else:
            live = [o for o in (self.cache.get("sessions.list") or [])
                    if not o.get("internal") and o.get("active") and (o.get("secs_running") or 0) >= 1]
            self._visible = live
            rows = [(T("agora"), T(o["op"]), T(o["ns"]), T(fmt_int((o.get("microsecs_running") or 0) // 1000)),
                     T("—"), T("—"), T("—"), T(o.get("plan_summary")), T(o.get("app_name") or o.get("client")),
                     T((o.get("command") or "")[:80])) for o in live]
            cols = ("ts", "op", "ns", "millis", "docsExam", "keysExam", "nret", "plano", "app/client", "comando")
        fill_table(self.query_one("#slow-table", DataTable), cols, rows)

    def action_detail(self) -> None:
        dt = self.query_one("#slow-table", DataTable)
        if 0 <= dt.cursor_row < len(self._visible):
            r = self._visible[dt.cursor_row]
            self.app.push_screen(TextViewScreen(f"Operação {r.get('ns')}", "\n".join(f"{k}: {v}" for k, v in r.items())))

    def _ask_db(self, then) -> None:
        from mongodb_brabo.widgets.input_modal import InputModal
        dbs = [d for d in (self.cache.get("profiler.levels") or {}) if d not in ("admin", "config", "local")]
        self.app.push_screen(InputModal("Banco de dados:", dbs[0] if dbs else ""), lambda db: db and then(db))

    def action_profiler_on(self) -> None:
        if not check_action("profiler_on", self.config, self.caps).allowed:
            self.request_admin_action("profiler_on", "admin", {"profile": 1}, "Profiler ON", "")
            return
        self._ask_db(lambda db: self.request_admin_action(
            "profiler_on", db, {"profile": 1, "slowms": 100}, "Profiler ON",
            f"Ligar o profiler nível 1 (slowms=100) no banco '{db}'? Gera escrita extra em {db}.system.profile."))

    def action_profiler_off(self) -> None:
        if not check_action("profiler_off", self.config, self.caps).allowed:
            self.request_admin_action("profiler_off", "admin", {"profile": 0}, "Profiler OFF", "")
            return
        self._ask_db(lambda db: self.request_admin_action(
            "profiler_off", db, {"profile": 0}, "Profiler OFF", f"Desligar o profiler no banco '{db}'?"))


# ---------------------------------------------------------------------------
# F4 LOCKS / FILAS
# ---------------------------------------------------------------------------

class LocksPanel(BasePanel):
    TITLE = "F4 LOCKS · FILAS"

    def compose(self) -> ComposeResult:
        yield Static(id="locks-summary")
        yield Static("[bold]Operações esperando lock[/]", id="locks-wait-title")
        yield DataTable(id="locks-wait", cursor_type="row")
        yield Static("[bold]lockStats (serverStatus.locks — acumulado desde o start)[/]")
        yield DataTable(id="locks-stats", cursor_type="row", zebra_stripes=True)

    async def refresh_data(self) -> None:
        na = self.not_available("health")
        q = self.cache.get("locks.queue") or {}
        ac = self.cache.get("locks.active_clients") or {}
        tk = self.cache.get("wt.tickets") or {}
        g = Table.grid(padding=(0, 3))
        for _ in range(3):
            g.add_column()
        qs = "bold red" if (q.get("total") or 0) >= 10 else ("yellow" if q.get("total") else "green")
        g.add_row(Text(f"Fila (globalLock.currentQueue): total {q.get('total', 0)}  "
                       f"leitores {q.get('readers', 0)}  escritores {q.get('writers', 0)}", style=qs),
                  Text(f"Clientes ativos: {ac.get('total', 0)} (R {ac.get('readers', 0)} / W {ac.get('writers', 0)})"),
                  Text(f"Tickets R {fmt_int((tk.get('read') or {}).get('out'))}/{fmt_int((tk.get('read') or {}).get('total'))}"
                       f"  W {fmt_int((tk.get('write') or {}).get('out'))}/{fmt_int((tk.get('write') or {}).get('total'))}"
                       f"  [{tk.get('source') or '—'}]", style="dim"))
        self.query_one("#locks-summary", Static).update(na or Panel(Group(self.banner(), g), border_style="cyan"))

        waiting = [o for o in (self.cache.get("sessions.list") or []) if o.get("waiting_for_lock")]
        fill_table(self.query_one("#locks-wait", DataTable),
                   ("opid", "op", "ns", "secs", "client", "desc", "comando"),
                   [(T(o["opid"]), T(o["op"]), T(o["ns"]), T(fmt_duration(o["secs_running"]), "red"),
                     T(o["client"]), T(o["desc"]), T((o.get("command") or "")[:90])) for o in waiting]
                   or [(T("—"), T("nenhuma operação esperando lock", "green"), T(""), T(""), T(""), T(""), T(""))])
        stats = self.cache.get("locks.stats") or []
        fill_table(self.query_one("#locks-stats", DataTable),
                   ("recurso", "modo", "acquireCount", "acquireWaitCount", "tempo espera", "deadlocks"),
                   [(T(s["resource"]), T(s["mode"]), T(fmt_int(s["acquire"])),
                     T(fmt_int(s["wait"]), "yellow" if s["wait"] else ""),
                     T(fmt_duration((s["wait_us"] or 0) / 1e6)), T(fmt_int(s["deadlocks"]))) for s in stats])


# ---------------------------------------------------------------------------
# F12 ADVISOR
# ---------------------------------------------------------------------------

class AdvisorPanel(BasePanel):
    TITLE = "F12 ADVISOR"

    def __init__(self, *a, **kw) -> None:
        super().__init__(*a, **kw)
        self._findings: list[dict] = []

    def compose(self) -> ComposeResult:
        yield Static(id="adv-label")
        yield DataTable(id="adv-table", cursor_type="row", zebra_stripes=True)
        yield Static(id="adv-detail")

    async def refresh_data(self) -> None:
        s = self.cache.get("advisor.summary") or {}
        self._findings = self.cache.get("advisor.findings") or []
        t = self.banner()
        t.append(f"   score {s.get('score', '—')}/100 · CRITICAL {s.get('critical', 0)} · WARNING {s.get('warning', 0)} "
                 f"· INFO {s.get('info', 0)}   (lê só o cache — zero carga extra no servidor)", style="dim")
        self.query_one("#adv-label", Static).update(t)
        fill_table(self.query_one("#adv-table", DataTable), ("sev", "id", "categoria", "achado", "evidência"),
                   [(T(f"{SEV_ICON.get(f['severity'], '')} {f['severity']}", SEV_STYLE.get(f["severity"], "")),
                     T(f["id"]), T(f["category"]), T(f["title"]), T(f["evidence"])) for f in self._findings]
                   or [(T("OK", "green"), T(""), T(""), T("Nenhum achado — todas as regras passaram."), T(""))])
        self._show_detail()

    def on_data_table_row_highlighted(self, _event) -> None:
        self._show_detail()

    def _show_detail(self) -> None:
        dt = self.query_one("#adv-table", DataTable)
        if not (0 <= dt.cursor_row < len(self._findings)):
            self.query_one("#adv-detail", Static).update("")
            return
        f = self._findings[dt.cursor_row]
        body = Text()
        body.append("O que é: ", style="bold")
        body.append(f["detail"] + "\n")
        body.append("Evidência: ", style="bold")
        body.append(f["evidence"] + "\n")
        body.append("Recomendação: ", style="bold green")
        body.append(f["recommendation"])
        self.query_one("#adv-detail", Static).update(
            Panel(body, title=f"[{SEV_STYLE.get(f['severity'], '')}]{f['id']} — {f['title']}[/]", border_style="blue"))
