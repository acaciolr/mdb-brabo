"""
core/permissions.py
Central gate for administrative actions. READ ONLY blocks everything
outright, regardless of the user's real privileges. DBA mode still checks
real privileges/topology (via Capabilities) before an action is offered —
the app must never present a button that is guaranteed to fail.

The full path of any administrative command is:
    panel key → BasePanel.request_admin_action()
              → check_action()         (this module)
              → ConfirmModal           (explicit Y)
              → ConnectionManager.execute_action()  (refuses READ_ONLY again)
"""

from __future__ import annotations

from dataclasses import dataclass

from mongodb_brabo.core.capabilities import Capabilities
from mongodb_brabo.core.config import AppConfig

# Every action here requires confirmation, even in DBA mode.
ADMIN_ACTIONS = {
    "kill_op",
    "step_down",
    "profiler_on",
    "profiler_off",
    "compact",
}


@dataclass
class ActionCheck:
    allowed: bool
    reason: str = ""


def check_action(action: str, config: AppConfig, caps: Capabilities) -> ActionCheck:
    if action not in ADMIN_ACTIONS:
        return ActionCheck(False, f"Ação desconhecida: {action}")
    if config.demo:
        return ActionCheck(False, "Modo DEMO — nenhuma ação é executada.")
    if config.is_readonly:
        return ActionCheck(False, "Conexão em modo READ ONLY (abra com --dba para ações administrativas).")
    if action == "kill_op" and not caps.can_kill_op:
        return ActionCheck(False, "Usuário sem a ação 'killop' (precisa de hostManager/clusterAdmin/root).")
    if action == "step_down" and not caps.can_step_down:
        return ActionCheck(False, "stepDown só é possível no PRIMARY de um replica set, com replSetStateChange.")
    if action in ("profiler_on", "profiler_off") and not caps.can_profile:
        return ActionCheck(False, "Profiler não se aplica a mongos/arbiter.")
    if action == "compact" and not caps.can_compact:
        return ActionCheck(False, "compact indisponível (mongos/arbiter ou usuário sem a ação 'compact').")
    return ActionCheck(True)
