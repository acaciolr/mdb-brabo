"""
widgets/panels_history.py
`h` — local history (SQLite ~/.mdb_brabo/history.db) of a few Dashboard
metrics, written every 60s by ConnectionSession from the cache. Shows the
last 24h and a naive linear growth projection of data size — but only with
at least a day of samples; otherwise it says it's still collecting.
"""

from __future__ import annotations

import asyncio
import time

from rich.console import Group
from rich.panel import Panel
from rich.text import Text
from textual.app import ComposeResult
from textual.containers import Horizontal
from textual.widgets import Static

from mongodb_brabo.core.connection_session import history_store
from mongodb_brabo.widgets.charts import Graph, fmt_bytes
from mongodb_brabo.widgets.panels import BasePanel

_RANGE = 24 * 3600


class HistoryPanel(BasePanel):
    TITLE = "h HISTÓRICO"
    REFRESH_RATE = 20
    DEFAULT_CSS = BasePanel.DEFAULT_CSS + """
    .hist-row { height: 11; }
    .hist-row Graph { width: 1fr; height: 11; margin: 0 1 0 0; }
    """

    def compose(self) -> ComposeResult:
        yield Static(id="hist-status")
        with Horizontal(classes="hist-row"):
            yield Graph("Conexões", color=(68, 180, 255), id="h-conn")
            yield Graph("Ops/s", color=(0, 237, 100), id="h-ops")
        with Horizontal(classes="hist-row"):
            yield Graph("WT cache %", color=(252, 213, 121), unit="%", id="h-wt")
            yield Graph("Dados (MB)", color=(191, 121, 252), id="h-data")
        yield Static(id="hist-proj")

    async def refresh_data(self) -> None:
        if self.config.demo:
            self.query_one("#hist-status", Static).update(Group(self.banner(), Panel(
                Text("Histórico não é gravado em modo DEMO (dados simulados não devem poluir history.db).",
                     style="yellow"), title="[bold yellow]NOT AVAILABLE[/]", border_style="yellow")))
            return
        key = self.session.history_key
        data = await asyncio.to_thread(self._load, key)
        avail = data["metrics"]
        msg = (f"Métricas: {', '.join(avail)} · chave {key} · amostras 24h: {len(data['conn'])}" if avail else
               "Coletando… o primeiro snapshot é gravado ~30s após conectar e depois a cada 60s "
               "(arquivo ~/.mdb_brabo/history.db, sobrevive a reinícios).")
        self.query_one("#hist-status", Static).update(Group(self.banner(), Text(msg, style="dim")))
        self.query_one("#h-conn", Graph).update_data(data["conn"])
        self.query_one("#h-ops", Graph).update_data(data["ops"])
        self.query_one("#h-wt", Graph).update_data(data["wt"])
        self.query_one("#h-data", Graph).update_data([v / 1024 / 1024 for v in data["data"]])
        p = data["proj"]
        if not p:
            txt = Text("Projeção de crescimento: dados insuficientes (precisa de ≥ 1 dia de histórico).", style="dim")
        else:
            txt = Text(f"Dados: atual {fmt_bytes(p['current'])} · tendência {fmt_bytes(p['slope_per_day'])}/dia · "
                       f"+30d {fmt_bytes(p['projected_30d'])} · +90d {fmt_bytes(p['projected_90d'])} "
                       f"({p['sample_count']} amostras em {p['span_days']}d, regressão linear simples)")
        self.query_one("#hist-proj", Static).update(Panel(txt, title="[bold]Capacidade[/]", border_style="yellow"))

    @staticmethod
    def _load(key: str) -> dict:
        st = history_store()
        since = time.time() - _RANGE

        def vals(m):
            return [v for _, v in st.query(key, m, since)]

        return {"metrics": st.available_metrics(key), "conn": vals("connections_current"),
                "ops": vals("ops_per_sec"), "wt": vals("wt_cache_used_pct"), "data": vals("data_size_bytes"),
                "proj": st.growth_projection(key, "data_size_bytes", 30)}
