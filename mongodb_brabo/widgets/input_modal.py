"""
widgets/input_modal.py
One-line prompt (e.g. "which database?" before turning the profiler on).
"""
from __future__ import annotations

from textual.app import ComposeResult
from textual.binding import Binding
from textual.containers import Vertical
from textual.screen import ModalScreen
from textual.widgets import Input, Label


class InputModal(ModalScreen[str | None]):
    BINDINGS = [Binding("escape", "cancel", "Cancelar")]
    DEFAULT_CSS = """
    InputModal { align: center middle; }
    InputModal > Vertical { width: 70; height: auto; background: #161b22; border: heavy #00ed64; padding: 1 2; }
    """

    def __init__(self, prompt: str, value: str = "", **kwargs) -> None:
        super().__init__(**kwargs)
        self._prompt, self._value = prompt, value

    def compose(self) -> ComposeResult:
        with Vertical():
            yield Label(self._prompt)
            yield Input(value=self._value, id="input-value")
            yield Label("[dim]Enter = ok   Esc = cancelar[/]")

    def on_input_submitted(self, event: Input.Submitted) -> None:
        self.dismiss(event.value.strip() or None)

    def action_cancel(self) -> None:
        self.dismiss(None)
