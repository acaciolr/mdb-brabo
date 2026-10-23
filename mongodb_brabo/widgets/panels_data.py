"""
widgets/panels_data.py
F5 Bancos/Coleções, F6 Índices, F9 WiredTiger, F10 Oplog.
"""

from __future__ import annotations

from rich.console import Group
from rich.panel import Panel
from rich.table import Table
from rich.text import Text
from textual.app import ComposeResult
from textual.binding import Binding
from textual.containers import Horizontal
from textual.widgets import DataTable, Input, Static

from mongodb_brabo.widgets.charts import Graph, fmt, fmt_bytes, fmt_duration, fmt_int, pct_bar
from mongodb_brabo.widgets.panels import BasePanel, T, fill_table


class DatabasesPanel(BasePanel):
    TITLE = "F5 BANCOS · COLEÇÕES"
    REFRESH_RATE = 3
    BINDINGS = [Binding("c", "compact", "compact (DBA)")]

    def __init__(self, *a, **kw) -> None:
        super().__init__(*a, **kw)
        self._colls: list[dict] = []

    def compose(self) -> ComposeResult:
        yield Static(id="db-label")
        yield DataTable(id="db-table", cursor_type="row", zebra_stripes=True)
        yield Static(id="coll-label")
        yield DataTable(id="coll-table", cursor_type="row", zebra_stripes=True)

    async def refresh_data(self) -> None:
        na = self.not_available("database")
        lbl = self.banner()
        lbl.append("   dbStats por banco (dataSize = descomprimido; storage = em disco, comprimido)", style="dim")
        self.query_one("#db-label", Static).update(Group(lbl, na) if na else lbl)
        dbs = self.cache.get("database.list") or []
        fill_table(self.query_one("#db-table", DataTable),
                   ("banco", "em disco", "dataSize", "storage", "coleções", "views", "documentos",
                    "avgObjSize", "índices", "tam. índices", "erro"),
                   [(T(d["name"], "bold"), T(fmt_bytes(d.get("size_on_disk"))), T(fmt_bytes(d.get("data_size"))),
                     T(fmt_bytes(d.get("storage_size"))), T(fmt_int(d.get("collections"))), T(fmt_int(d.get("views"))),
                     T(fmt_int(d.get("objects"))), T(fmt_bytes(d.get("avg_obj_size"))), T(fmt_int(d.get("indexes"))),
                     T(fmt_bytes(d.get("index_size"))), T(d.get("error"), "red")) for d in dbs])
        self._colls = self.cache.get("database.collections") or []
        trunc = " (TRUNCADO: limite de coleções por ciclo)" if self.cache.get("database.truncated") else ""
        self.query_one("#coll-label", Static).update(
            Text(f"Coleções por tamanho em disco ($collStats){trunc}   C = compact (DBA) na coleção selecionada",
                 style="bold"))
        fill_table(self.query_one("#coll-table", DataTable),
                   ("namespace", "documentos", "size", "storage", "índices", "tam. índices", "avgObjSize", "capped", "erro"),
                   [(T(c["ns"]), T(fmt_int(c.get("count"))), T(fmt_bytes(c.get("size"))),
                     T(fmt_bytes(c.get("storage_size"))), T(fmt_int(c.get("nindexes"))),
                     T(fmt_bytes(c.get("total_index_size"))), T(fmt_bytes(c.get("avg_obj_size"))),
                     T("sim" if c.get("capped") else ""), T(c.get("error"), "red")) for c in self._colls])

    def action_compact(self) -> None:
        dt = self.query_one("#coll-table", DataTable)
        if not (0 <= dt.cursor_row < len(self._colls)):
            self.app.notify("Selecione uma coleção na tabela de coleções.", severity="warning")
            return
        c = self._colls[dt.cursor_row]
        self.request_admin_action(
            "compact", c["db"], {"compact": c["name"]}, "compact",
            f"Rodar compact em {c['ns']}? Pode demorar e gerar I/O alto; em versões < 4.4 bloqueia o banco. "
            "Em replica set rode primeiro nos secundários.")


class IndexesPanel(BasePanel):
    TITLE = "F6 ÍNDICES · $indexStats"
    REFRESH_RATE = 3
    BINDINGS = [Binding("u", "toggle_unused", "Só não usados"), Binding("slash", "toggle_filter", "Filtro")]

    def __init__(self, *a, **kw) -> None:
        super().__init__(*a, **kw)
        self._only_unused = False

    def compose(self) -> ComposeResult:
        yield Static(id="idx-label")
        yield Input(placeholder="Filtro: namespace / nome do índice…", id="idx-filter", classes="hidden")
        yield DataTable(id="idx-table", cursor_type="row", zebra_stripes=True)

    async def refresh_data(self) -> None:
        na = self.not_available("indexes")
        idx = self.cache.get("indexes.list") or []
        unused = self.cache.get("indexes.unused") or []
        flt = self.query_one("#idx-filter", Input).value.lower().strip()
        rows = unused if self._only_unused else idx
        if flt:
            rows = [i for i in rows if flt in i["ns"].lower() or flt in i["name"].lower()]
        rows = sorted(rows, key=lambda i: (not i["unused"], i["ops"]))
        lbl = self.banner()
        lbl.append(f"   {len(idx)} índices · {len(unused)} sem uso desde o restart"
                   f"{' · mostrando só não usados' if self._only_unused else ''}   U=só não usados /=filtro\n",
                   style="dim")
        lbl.append("   Contadores zeram no restart e são POR NÓ — confira todos os membros antes de remover.",
                   style="dim yellow")
        self.query_one("#idx-label", Static).update(Group(lbl, na) if na else lbl)
        fill_table(self.query_one("#idx-table", DataTable),
                   ("namespace", "índice", "chave", "ops", "desde", "janela", "tamanho", "flags", "status"),
                   [(T(i["ns"]), T(i["name"]), T(i.get("key")), T(fmt_int(i["ops"])), T(i.get("since")),
                     T(fmt_duration((i.get("tracked_hours") or 0) * 3600)), T(fmt_bytes(i.get("size"))),
                     T(i.get("flags")), T("NUNCA USADO", "bold yellow") if i["unused"] else T("em uso", "green"))
                    for i in rows])

    def action_toggle_unused(self) -> None:
        self._only_unused = not self._only_unused
        self._last_refresh = 0

    def action_toggle_filter(self) -> None:
        inp = self.query_one("#idx-filter", Input)
        if inp.has_class("hidden"):
            inp.remove_class("hidden")
            inp.focus()
        else:
            inp.add_class("hidden")
            inp.value = ""
            self.query_one("#idx-table").focus()


class WiredTigerPanel(BasePanel):
    TITLE = "F9 WIREDTIGER"
    DEFAULT_CSS = BasePanel.DEFAULT_CSS + """
    #wt-charts { height: 11; }
    #wt-charts Graph { width: 1fr; height: 11; margin: 0 1 0 0; }
    """

    def compose(self) -> ComposeResult:
        yield Static(id="wt-body")
        with Horizontal(id="wt-charts"):
            yield Graph("Cache usado %", color=(252, 213, 121), unit="%", id="g-wt-used")
            yield Graph("Dirty %", color=(255, 120, 120), unit="%", id="g-wt-dirty")
            yield Graph("Evictions/s", color=(68, 180, 255), id="g-wt-evict")

    async def refresh_data(self) -> None:
        na = self.not_available("wt")
        if na:
            self.query_one("#wt-body", Static).update(Group(self.banner(), na))
            return
        c = self.cache.get("wt.cache") or {}
        tk = self.cache.get("wt.tickets") or {}
        cp = self.cache.get("wt.checkpoint") or {}
        r = self.cache.get("health.rates") or {}

        a = Table.grid(padding=(0, 1))
        a.add_column(width=20)
        a.add_column()
        a.add_row("[dim]Em cache[/]", f"{fmt_bytes(c.get('bytes_in_cache'))} de {fmt_bytes(c.get('max_bytes'))}")
        a.add_row("[dim]Usado[/]", pct_bar(c.get("used_pct") or 0, 20, 80, 95))
        a.add_row("[dim]Dirty[/]", pct_bar(c.get("dirty_pct") or 0, 20, 5, 20))
        a.add_row("[dim]Lido p/ cache[/]", fmt_bytes(c.get("read_into")))
        a.add_row("[dim]Gravado do cache[/]", fmt_bytes(c.get("written_from")))
        a.add_row("[dim]Evictions/s[/]", fmt(r.get("evictions")))
        app_ev = r.get("app_evictions")
        a.add_row("[dim]Evict. por app/s[/]", Text(fmt(app_ev), style="bold red" if (app_ev or 0) > 0 else ""))

        b = Table.grid(padding=(0, 1))
        b.add_column(width=18)
        b.add_column()
        for kind, label in (("read", "Leitura"), ("write", "Escrita")):
            k = tk.get(kind) or {}
            b.add_row(f"[dim]Tickets {label}[/]", f"em uso {fmt_int(k.get('out'))} · livres "
                                                  f"{fmt_int(k.get('available'))} · total {fmt_int(k.get('total'))}")
        b.add_row("[dim]Fonte[/]", tk.get("source") or "—")
        b.add_row("", "")
        b.add_row("[dim]Checkpoints[/]", fmt_int(cp.get("count")))
        b.add_row("[dim]Rodando agora[/]", "sim" if cp.get("running") else "não")
        b.add_row("[dim]Último (ms)[/]", fmt_int(cp.get("last_ms")))
        b.add_row("[dim]Máximo (ms)[/]", fmt_int(cp.get("max_ms")))

        g = Table.grid(expand=True, padding=(0, 1))
        g.add_column(ratio=1)
        g.add_column(ratio=1)
        g.add_row(Panel(a, title="[bold yellow]Cache[/]", border_style="yellow"),
                  Panel(b, title="[bold cyan]Tickets · Checkpoints[/]", border_style="cyan"))
        self.query_one("#wt-body", Static).update(Group(self.banner(), g))
        hist = [h for h in self.cache.get_history_values("wt.cache") if isinstance(h, dict)]
        self.query_one("#g-wt-used", Graph).update_data([h.get("used_pct") or 0 for h in hist])
        self.query_one("#g-wt-dirty", Graph).update_data([h.get("dirty_pct") or 0 for h in hist])
        rates = [x for x in self.cache.get_history_values("health.rates") if isinstance(x, dict)]
        self.query_one("#g-wt-evict", Graph).update_data([x.get("evictions") or 0 for x in rates
                                                          if x.get("evictions") is not None])


class OplogPanel(BasePanel):
    TITLE = "F10 OPLOG"
    REFRESH_RATE = 2

    def compose(self) -> ComposeResult:
        yield Static(id="oplog-body")

    async def refresh_data(self) -> None:
        na = self.not_available("oplog")
        if na:
            self.query_one("#oplog-body", Static).update(Group(self.banner(), na))
            return
        o = self.cache.get("oplog.info") or {}
        g = Table.grid(padding=(0, 2))
        g.add_column(width=26)
        g.add_column()
        hours = o.get("window_hours")
        style = "green" if (hours or 0) >= 24 else ("yellow" if (hours or 0) >= 4 else "red")
        g.add_row("[dim]Janela (first→last)[/]", Text(f"{fmt(hours, 2)} h", style=f"bold {style}"))
        g.add_row("[dim]Primeira entrada[/]", o.get("first") or "—")
        g.add_row("[dim]Última entrada[/]", o.get("last") or "—")
        g.add_row("[dim]Tamanho usado / máximo[/]", f"{fmt_bytes(o.get('used_bytes'))} / {fmt_bytes(o.get('max_bytes'))}")
        g.add_row("[dim]Ocupação[/]", pct_bar(o.get("used_pct") or 0, 24, 101, 101))
        g.add_row("[dim]Em disco (comprimido)[/]", fmt_bytes(o.get("storage_bytes")))
        g.add_row("[dim]Entradas[/]", fmt_int(o.get("count")))
        g.add_row("[dim]Geração média[/]", f"{fmt_bytes(o.get('avg_bytes_per_hour'))}/h (usado ÷ janela)")
        live = o.get("live_bytes_per_hour")
        g.add_row("[dim]Geração ao vivo[/]", f"{fmt_bytes(live)}/h" if live is not None
                  else "— (só medível enquanto o oplog não encheu)")
        if (o.get("used_pct") or 0) < 90:
            g.add_row("[dim]Janela projetada[/]", f"{fmt(o.get('projected_window_hours'))} h quando encher "
                                                  "(no ritmo médio atual)")
        self.query_one("#oplog-body", Static).update(Group(
            self.banner(), Panel(g, title="[bold]local.oplog.rs[/]", border_style="cyan"),
            Text("Regra prática: a janela deve cobrir com folga a maior manutenção/parada de um secundário "
                 "(≥ 24h). Ajuste com replSetResizeOplog ou storage.oplogMinRetentionHours.", style="dim")))
