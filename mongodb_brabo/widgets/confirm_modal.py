"""
widgets/confirm_modal.py
Yes/no confirmation for every administrative action (killOp, stepDown,
profiler, compact). Shows the exact command that will be sent — the DBA
confirms the command, not a vague description. Focus starts on "Cancelar".
"""
from __future__ import annotations

from textual.app import ComposeResult
from textual.binding import Binding
from textual.containers import Horizontal, Vertical
from textual.screen import ModalScreen
from textual.widgets import Button, Label


class ConfirmModal(ModalScreen[bool]):
    BINDINGS = [
        Binding("escape", "answer(False)", "Cancelar"),
        Binding("n", "answer(False)", "Não"),
        Binding("y", "answer(True)", "Sim"),
        Binding("s", "answer(True)", "Sim", show=False),
    ]

    DEFAULT_CSS = """
    ConfirmModal { align: center middle; }
    ConfirmModal > Vertical { width: 76; height: auto; background: #161b22; border: heavy #f85149; padding: 1 2; }
    ConfirmModal #confirm-title { text-style: bold; color: #f85149; margin-bottom: 1; }
    ConfirmModal #confirm-cmd { color: #e3b341; margin: 1 0; }
    ConfirmModal #confirm-hint { color: #8b949e; }
    ConfirmModal Horizontal { height: auto; align: right middle; margin-top: 1; }
    ConfirmModal Button { margin-left: 1; }
    """

    def __init__(self, title: str, body: str, command: str = "", **kwargs) -> None:
        super().__init__(**kwargs)
        self._title, self._body, self._command = title, body, command

    def compose(self) -> ComposeResult:
        with Vertical():
            yield Label(self._title, id="confirm-title")
            yield Label(self._body, id="confirm-body")
            if self._command:
                yield Label(self._command, id="confirm-cmd", markup=False)
            yield Label("Y/S = confirmar   N/Esc = cancelar", id="confirm-hint")
            with Horizontal():
                yield Button("Cancelar", variant="default", id="btn-no")
                yield Button("Confirmar", variant="error", id="btn-yes")

    def on_mount(self) -> None:
        self.query_one("#btn-no", Button).focus()

    def on_button_pressed(self, event: Button.Pressed) -> None:
        self.dismiss(event.button.id == "btn-yes")

    def action_answer(self, value: bool) -> None:
        self.dismiss(value)
