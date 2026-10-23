"""
widgets/add_connection_modal.py
New-connection dialog: either a full URI or host/port/user/password.
Optionally saves the profile (WITHOUT the password; the password goes to
the keyring when one is available — see core/connections_store.py).
"""
from __future__ import annotations

from textual.app import ComposeResult
from textual.binding import Binding
from textual.containers import Horizontal, Vertical
from textual.screen import ModalScreen
from textual.widgets import Button, Checkbox, Input, Label

from mongodb_brabo.core.config import AppConfig


class AddConnectionModal(ModalScreen[AppConfig | None]):
    BINDINGS = [Binding("escape", "cancel", "Cancelar")]
    DEFAULT_CSS = """
    AddConnectionModal { align: center middle; }
    AddConnectionModal > Vertical { width: 84; height: auto; background: #161b22; border: heavy #00ed64; padding: 1 2; }
    AddConnectionModal Input { margin-bottom: 0; }
    AddConnectionModal Horizontal { height: auto; }
    AddConnectionModal #row-host Input { width: 1fr; }
    AddConnectionModal #buttons { align: right middle; margin-top: 1; }
    AddConnectionModal Button { margin-left: 1; }
    """

    def compose(self) -> ComposeResult:
        with Vertical():
            yield Label("[bold #00ed64]Nova conexão MongoDB[/]")
            yield Label("[dim]URI (opcional — se preenchida, ignora host/porta/usuário):[/]")
            yield Input(placeholder="mongodb://user@host:27017/?authSource=admin&directConnection=true", id="f-uri")
            with Horizontal(id="row-host"):
                yield Input(placeholder="host", value="localhost", id="f-host")
                yield Input(placeholder="porta", value="27017", id="f-port")
                yield Input(placeholder="auth db", value="admin", id="f-authdb")
            with Horizontal(id="row-host"):
                yield Input(placeholder="usuário", id="f-user")
                yield Input(placeholder="senha", password=True, id="f-pass")
            yield Input(placeholder="rótulo da aba (opcional)", id="f-label")
            with Horizontal():
                yield Checkbox("directConnection", value=True, id="f-direct")
                yield Checkbox("modo DBA", value=False, id="f-dba")
                yield Checkbox("salvar perfil", value=False, id="f-save")
            with Horizontal(id="buttons"):
                yield Button("Cancelar", id="btn-cancel")
                yield Button("Conectar", variant="success", id="btn-ok")

    def _value(self, wid: str) -> str:
        return self.query_one(f"#{wid}", Input).value.strip()

    def on_button_pressed(self, event: Button.Pressed) -> None:
        if event.button.id == "btn-cancel":
            self.dismiss(None)
            return
        self._submit()

    def on_input_submitted(self, _event) -> None:
        self._submit()

    def _submit(self) -> None:
        try:
            port = int(self._value("f-port") or 27017)
        except ValueError:
            self.app.notify("Porta inválida", severity="error")
            return
        cfg = AppConfig(
            uri=self._value("f-uri") or None, host=self._value("f-host") or "localhost", port=port,
            user=self._value("f-user") or None, password=self.query_one("#f-pass", Input).value or None,
            auth_db=self._value("f-authdb") or "admin", label=self._value("f-label") or None,
            direct=self.query_one("#f-direct", Checkbox).value,
            mode="DBA" if self.query_one("#f-dba", Checkbox).value else "READ_ONLY",
        )
        if self.query_one("#f-save", Checkbox).value and not cfg.uri:
            from mongodb_brabo.core.connections_store import SavedConnection, save_connection
            label = cfg.label or f"{cfg.host}:{cfg.port}"
            stored = save_connection(SavedConnection(label=label, host=cfg.host, port=cfg.port, user=cfg.user,
                                                     auth_db=cfg.auth_db, mode=cfg.mode, direct=cfg.direct),
                                     cfg.password)
            self.app.notify(f"Perfil '{label}' salvo" + ("" if stored or not cfg.password else
                            " (senha NÃO salva: sem keyring — use MDB_BRABO_PASSWORD)"))
        self.dismiss(cfg)

    def action_cancel(self) -> None:
        self.dismiss(None)
