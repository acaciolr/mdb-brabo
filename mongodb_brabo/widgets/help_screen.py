"""
widgets/help_screen.py
Key bindings overlay (?).
"""
from __future__ import annotations

from rich.table import Table
from textual.app import ComposeResult
from textual.binding import Binding
from textual.screen import ModalScreen
from textual.widgets import Static

_ROWS = [
    ("F1", "Dashboard — serverStatus, conexões, ops/s, WiredTiger, gráficos, saúde", "Painéis"),
    ("F2", "Sessões — $currentOp (D detalhe, K kill, / filtro, I internas)", "Painéis"),
    ("F3", "Top operações — system.profile (P/O liga/desliga profiler em DBA)", "Painéis"),
    ("F4", "Locks / filas — globalLock.currentQueue, waitingForLock, lockStats", "Painéis"),
    ("F5", "Bancos e coleções — dbStats/$collStats (C compact em DBA)", "Painéis"),
    ("F6", "Índices — $indexStats (U só não usados, / filtro)", "Painéis"),
    ("F7", "Replica Set — membros, lag, eleição (S stepDown em DBA)", "Painéis"),
    ("F8", "Sharding — shards, balancer, chunks (via mongos)", "Painéis"),
    ("F9", "WiredTiger — cache, dirty, evictions, tickets, checkpoints", "Painéis"),
    ("F10", "Oplog — tamanho, janela, taxa de geração", "Painéis"),
    ("F11", "Segurança — usuários, roles, authorization, TLS", "Painéis"),
    ("F12", "Advisor — achados CRITICAL/WARNING/INFO com evidência", "Painéis"),
    ("h", "Histórico local (SQLite) e projeção", "Painéis"),
    ("+ / Ctrl+N", "Nova conexão (nova aba)", "Abas"),
    ("Ctrl+W", "Fechar aba", "Abas"),
    ("Tab", "Mover foco (tabela ↔ abas)", "Abas"),
    ("?", "Esta ajuda", "App"),
    ("q", "Sair", "App"),
]


class HelpScreen(ModalScreen):
    BINDINGS = [Binding("escape", "close", "Fechar"), Binding("q", "close", show=False),
                Binding("question_mark", "close", show=False)]
    DEFAULT_CSS = """
    HelpScreen { align: center middle; }
    HelpScreen > Static { width: 92; height: auto; background: #161b22; border: solid #00ed64; padding: 1 2; }
    """

    def compose(self) -> ComposeResult:
        t = Table(show_header=True, header_style="bold green", box=None, expand=True)
        t.add_column("Tecla", width=12)
        t.add_column("Ação", ratio=3)
        t.add_column("Grupo", width=8)
        for k, d, g in _ROWS:
            t.add_row(f"[bold yellow]{k}[/]", d, f"[dim]{g}[/]")
        t.add_row("", "", "")
        t.add_row("", "[dim]READ ONLY por padrão. Ações (killOp, stepDown, profiler, compact) só com --dba "
                      "e sempre com confirmação.[/]", "")
        yield Static(t)

    def action_close(self) -> None:
        self.dismiss()
