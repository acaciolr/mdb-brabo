"""
widgets/panels_cluster.py
F7 Replica Set, F8 Sharding, F11 Segurança.
"""

from __future__ import annotations

from rich.console import Group
from rich.panel import Panel
from rich.table import Table
from rich.text import Text
from textual.app import ComposeResult
from textual.binding import Binding
from textual.widgets import DataTable, Static

from mongodb_brabo.widgets.charts import fmt_bytes, fmt_duration, fmt_int
from mongodb_brabo.widgets.panels import BasePanel, T, fill_table

_STATE_STYLE = {"PRIMARY": "bold green", "SECONDARY": "cyan", "ARBITER": "dim"}


class ReplicationPanel(BasePanel):
    TITLE = "F7 REPLICA SET"
    REFRESH_RATE = 2
    BINDINGS = [Binding("s", "step_down", "stepDown (DBA)")]

    def compose(self) -> ComposeResult:
        yield Static(id="rs-head")
        yield DataTable(id="rs-table", cursor_type="row", zebra_stripes=True)

    async def refresh_data(self) -> None:
        na = self.not_available("replication")
        st = self.cache.get("replication.status") or {}
        o = self.cache.get("oplog.info") or {}
        if na:
            self.query_one("#rs-head", Static).update(Group(self.banner(), na))
            fill_table(self.query_one("#rs-table", DataTable), ("membro",), [])
            return
        g = Table.grid(padding=(0, 3))
        for _ in range(4):
            g.add_column()
        g.add_row(Text(f"set: {st.get('set')}", style="bold"),
                  Text(f"PRIMARY: {st.get('primary') or 'NENHUM'}", style="green" if st.get("primary") else "bold red"),
                  Text(f"term {st.get('term')} · heartbeat {st.get('heartbeat_ms')}ms"),
                  Text(f"oplog window: {o.get('window_hours', '—')}h"))
        g.add_row(Text(f"última eleição: {st.get('last_election') or '—'} {st.get('last_election_reason') or ''}",
                       style="dim"),
                  Text(f"lag máx: {fmt_duration(st.get('max_lag_seconds'))} (ref: {st.get('lag_reference')})"),
                  Text(f"não saudáveis: {', '.join(st.get('unhealthy') or []) or 'nenhum'}",
                       style="red" if st.get("unhealthy") else "green"),
                  Text("S = stepDown (DBA, só no PRIMARY)", style="dim"))
        self.query_one("#rs-head", Static).update(Group(self.banner(), Panel(g, border_style="cyan")))
        rows = []
        for m in st.get("members") or []:
            lag = m.get("lag_seconds")
            lag_style = "red" if (lag or 0) >= 60 else ("yellow" if (lag or 0) >= 10 else "green")
            rows.append((T(m["id"]), T(("★ " if m.get("self") else "") + str(m["name"])),
                         T(m["state"], _STATE_STYLE.get(m["state"], "bold red")),
                         T(m["health"], "green" if m["health"] == 1 else "bold red"),
                         T(fmt_duration(m.get("uptime"))), T(m.get("optime")),
                         T(fmt_duration(lag) if lag is not None else "—", lag_style),
                         T(m.get("sync_source")), T(fmt_int(m.get("ping_ms"))),
                         T(f"{m.get('priority', '—')}/{m.get('votes', '—')}{' hidden' if m.get('hidden') else ''}"),
                         T(m.get("last_heartbeat_msg"), "red")))
        fill_table(self.query_one("#rs-table", DataTable),
                   ("_id", "membro", "estado", "health", "uptime", "optime", "lag", "sync source", "ping ms",
                    "prio/votos", "heartbeat msg"), rows)

    def action_step_down(self) -> None:
        self.request_admin_action(
            "step_down", "admin", {"replSetStepDown": 60, "secondaryCatchUpPeriodSecs": 10}, "replSetStepDown",
            "Rebaixar este PRIMARY por 60s e forçar uma eleição? Escritas ficam indisponíveis por alguns segundos "
            "e conexões dos clientes serão derrubadas.")


class ShardingPanel(BasePanel):
    TITLE = "F8 SHARDING"
    REFRESH_RATE = 3

    def compose(self) -> ComposeResult:
        yield Static(id="sh-head")
        yield DataTable(id="sh-shards", cursor_type="row")
        yield Static("[bold]Distribuição de chunks por coleção[/]", id="sh-chunks-title")
        yield DataTable(id="sh-chunks", cursor_type="row", zebra_stripes=True)

    async def refresh_data(self) -> None:
        na = self.not_available("sharding")
        if na:
            self.query_one("#sh-head", Static).update(Group(self.banner(), na))
            return
        b = self.cache.get("sharding.balancer") or {}
        bal = Text(f"Balancer: modo={b.get('mode', '—')} · em rodada={'sim' if b.get('in_round') else 'não'} "
                   f"· rodadas={fmt_int(b.get('rounds'))}", style="green" if b.get("mode") == "full" else "bold yellow")
        if b.get("error"):
            bal.append(f"   ({b['error']})", style="red")
        self.query_one("#sh-head", Static).update(Group(self.banner(), bal))
        shards = self.cache.get("sharding.shards") or []
        fill_table(self.query_one("#sh-shards", DataTable), ("shard", "host", "state", "draining", "tags"),
                   [(T(s["id"], "bold"), T(s["host"]), T(s.get("state")),
                     T("SIM", "yellow") if s.get("draining") else T("não"), T(", ".join(s.get("tags") or [])))
                    for s in shards])
        colls = self.cache.get("sharding.collections") or []
        names = [s["id"] for s in shards]
        fill_table(self.query_one("#sh-chunks", DataTable), ("namespace", "shard key", "total", *names, "spread"),
                   [(T(c["ns"]), T(c.get("key")), T(fmt_int(c["total"])),
                     *[T(fmt_int(c["per_shard"].get(n, 0))) for n in names],
                     T(fmt_int(c["spread"]), "yellow" if c["spread"] >= 8 else "green")) for c in colls])


class SecurityPanel(BasePanel):
    TITLE = "F11 SEGURANÇA"
    REFRESH_RATE = 3

    def compose(self) -> ComposeResult:
        yield Static(id="sec-head")
        yield Static("[bold]Usuários (usersInfo forAllDBs)[/]")
        yield DataTable(id="sec-users", cursor_type="row", zebra_stripes=True)
        yield Static("[bold]Roles customizadas[/]")
        yield DataTable(id="sec-roles", cursor_type="row")

    async def refresh_data(self) -> None:
        na = self.not_available("security")
        o = self.cache.get("security.options") or {}
        errs = self.cache.get("security.errors") or {}
        mech = self.cache.get("security.mechanisms")
        sess = self.cache.get("security.session") or {}
        g = Table.grid(padding=(0, 2))
        g.add_column(width=24)
        g.add_column()
        if o.get("known"):
            g.add_row("[dim]authorization[/]", Text(str(o.get("authorization")),
                                                   style="green" if o.get("auth_enabled") else "bold red"))
            g.add_row("[dim]keyFile / clusterAuthMode[/]", f"{'sim' if o.get('key_file') else 'não'} / "
                                                           f"{o.get('cluster_auth_mode') or '—'}")
            g.add_row("[dim]TLS (net.tls.mode)[/]", Text(str(o.get("tls_mode")),
                                                        style="green" if o.get("tls_mode") not in ("disabled", None) else "yellow"))
            g.add_row("[dim]bindIp[/]", str(o.get("bind_ip") or "—"))
            g.add_row("[dim]auditLog[/]", "sim" if o.get("audit_log") else "não")
            g.add_row("[dim]config / argv[/]", Text(f"{o.get('config_file') or '—'}  ·  {o.get('argv')}"))
        else:
            g.add_row("[dim]authorization / TLS[/]",
                      Text("DESCONHECIDO — sem permissão para getCmdLineOpts (não significa desligado)", style="yellow"))
        g.add_row("[dim]Mecanismos de auth[/]", ", ".join(mech) if mech else "—")
        g.add_row("[dim]Sessão atual[/]", f"{', '.join(sess.get('users') or []) or '(sem auth)'}  roles: "
                                         f"{', '.join(sess.get('roles') or []) or '—'}")
        parts = [self.banner()]
        if na:
            parts.append(na)
        parts.append(Panel(g, border_style="cyan"))
        if errs:
            parts.append(Text("Sem acesso: " + "; ".join(f"{k}: {v}" for k, v in errs.items()), style="dim yellow"))
        self.query_one("#sec-head", Static).update(Group(*parts))
        users = self.cache.get("security.users") or []
        fill_table(self.query_one("#sec-users", DataTable), ("usuário", "db", "roles", "mecanismos"),
                   [(T(u["user"], "bold"), T(u["db"]),
                     T(", ".join(u["roles"]), "bold red" if any(r.startswith("root@") for r in u["roles"]) else ""),
                     T(", ".join(u["mechanisms"]))) for u in users])
        roles = self.cache.get("security.roles") or []
        fill_table(self.query_one("#sec-roles", DataTable), ("role", "db", "herda de"),
                   [(T(r["role"]), T(r["db"]), T(", ".join(r["inherits"]))) for r in roles]
                   or [(T("—"), T(""), T("nenhuma role customizada no admin"))])
