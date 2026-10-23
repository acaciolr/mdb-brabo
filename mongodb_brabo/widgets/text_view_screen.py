"""
widgets/text_view_screen.py
Full-screen scrollable text viewer (operation detail, profiler entry, ...).
"""
from __future__ import annotations

from textual.app import ComposeResult
from textual.binding import Binding
from textual.containers import VerticalScroll
from textual.screen import Screen
from textual.widgets import Footer, Header, Static


class TextViewScreen(Screen):
    BINDINGS = [Binding("q", "close", "Fechar"), Binding("escape", "close", "Fechar", show=False)]

    def __init__(self, title: str, content: str, **kwargs) -> None:
        super().__init__(**kwargs)
        self._t, self._c = title, content

    def compose(self) -> ComposeResult:
        yield Header(show_clock=False)
        with VerticalScroll():
            yield Static(self._c, markup=False, id="textview-body")
        yield Footer()

    def on_mount(self) -> None:
        self.title = self._t
        self.sub_title = "q / Esc para fechar"

    def action_close(self) -> None:
        self.dismiss()
