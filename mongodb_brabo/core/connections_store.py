"""
core/connections_store.py
Saved connection profiles in ~/.mdb_brabo/connections.json.

Format (a plain JSON list, editable by hand / by the platform installer):
    [
      {"label": "NODE01", "host": "192.168.60.1", "port": 27017,
       "user": "root_admin", "auth_db": "admin", "mode": "READ_ONLY",
       "direct": true}
    ]

The password is NEVER written to this file. Resolution order at load time:
  1. system keyring (service "mdb_brabo", key "label|host|port|user"), if
     the keyring package and a backend are available;
  2. env MDB_BRABO_PASSWORD_<LABEL> (label upper-cased, non-alnum → _);
  3. env MDB_BRABO_PASSWORD.
"""

from __future__ import annotations

import json
import logging
import os
import re
from dataclasses import asdict, dataclass, fields

from mongodb_brabo.core.config import AppConfig
from mongodb_brabo.core.paths import brabo_home

log = logging.getLogger(__name__)

_KEYRING_SERVICE = "mdb_brabo"

try:
    import keyring as _keyring
    _HAS_KEYRING = True
except ImportError:  # optional dependency
    _keyring = None
    _HAS_KEYRING = False


def store_file():
    return brabo_home() / "connections.json"


@dataclass
class SavedConnection:
    label: str
    host: str = "localhost"
    port: int = 27017
    user: str | None = None
    auth_db: str = "admin"
    mode: str = "READ_ONLY"
    direct: bool = False
    tls: bool = False
    refresh_interval: int = 5

    def _key(self) -> str:
        return f"{self.label}|{self.host}|{self.port}|{self.user or ''}"

    def resolved_password(self) -> str | None:
        if _HAS_KEYRING:
            try:
                pw = _keyring.get_password(_KEYRING_SERVICE, self._key())
                if pw:
                    return pw
            except Exception as exc:  # no backend on a headless SSH box is normal
                log.debug("keyring unavailable: %s", exc)
        env_label = "MDB_BRABO_PASSWORD_" + re.sub(r"[^A-Za-z0-9]", "_", self.label).upper()
        return os.environ.get(env_label) or os.environ.get("MDB_BRABO_PASSWORD")

    def to_app_config(self) -> AppConfig:
        return AppConfig(
            label=self.label or None, host=self.host, port=int(self.port), user=self.user or None,
            password=self.resolved_password() if self.user else None, auth_db=self.auth_db or "admin",
            mode=(self.mode or "READ_ONLY").upper(), direct=bool(self.direct), tls=bool(self.tls),
            refresh_interval=int(self.refresh_interval or 5),
        )


def load_connections() -> list[SavedConnection]:
    path = store_file()
    if not path.exists():
        return []
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
        known = {f.name for f in fields(SavedConnection)}
        out = []
        for item in data:
            if "password" in item:
                log.warning("connections.json: campo 'password' ignorado em %s (nunca usamos senha em claro)",
                            item.get("label"))
            out.append(SavedConnection(**{k: v for k, v in item.items() if k in known}))
        return out
    except Exception as exc:
        log.warning("Failed to load connections: %s", exc)
        return []


def save_connection(conn: SavedConnection, password: str | None = None) -> bool:
    """Persist the profile. Returns True if the password went to the keyring
    (False means the user must provide it via environment next time)."""
    stored = False
    if password and _HAS_KEYRING:
        try:
            _keyring.set_password(_KEYRING_SERVICE, conn._key(), password)
            stored = True
        except Exception as exc:
            log.info("keyring store failed (%s) — password not persisted", exc)
    conns = [c for c in load_connections() if c.label != conn.label]
    conns.insert(0, conn)
    path = store_file()
    path.write_text(json.dumps([asdict(c) for c in conns], indent=2, ensure_ascii=False), encoding="utf-8")
    try:
        path.chmod(0o600)
    except OSError:
        pass
    return stored


def select(saved: list[SavedConnection], spec: str) -> tuple[list[SavedConnection], list[str]]:
    """--start-saved spec: 'all' or comma-separated labels (case-insensitive)."""
    if spec.strip().lower() == "all":
        return list(saved), []
    chosen, missing = [], []
    for want in (s.strip() for s in spec.split(",") if s.strip()):
        match = next((c for c in saved if (c.label or "").lower() == want.lower()), None)
        (chosen.append(match) if match else missing.append(want))
    return chosen, missing
