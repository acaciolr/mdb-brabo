"""
core/config.py
Application configuration — one AppConfig per MongoDB connection (one tab).
Built from CLI args (--uri or --host/--port/...), a saved profile
(~/.mdb_brabo/connections.json) or the Add Connection modal.
"""

from __future__ import annotations

from dataclasses import dataclass
from urllib.parse import quote_plus


@dataclass
class AppConfig:
    # ── Connection: either a full URI, or the discrete fields below ────
    uri: str | None = None
    host: str = "localhost"
    port: int = 27017
    user: str | None = None
    password: str | None = None
    auth_db: str = "admin"
    direct: bool = False             # directConnection=true (talk to THIS node only)
    tls: bool = False

    # ── Display ────────────────────────────────────────────────────────
    label: str | None = None

    # ── Demo mode: populate the cache with simulated, clearly-marked data
    demo: bool = False

    # ── Behaviour ─────────────────────────────────────────────────────
    refresh_interval: int = 5
    # Hard ceilings for every round-trip. A monitoring tool that hangs on a
    # sick server is worse than useless, so socket/selection timeouts are
    # short and aggregations also carry maxTimeMS (see ConnectionManager).
    server_selection_timeout_ms: int = 5000
    connect_timeout_ms: int = 5000
    socket_timeout_ms: int = 15000
    query_max_time_ms: int = 5000
    app_name: str = "mdb_brabo"

    # ── Permission mode ────────────────────────────────────────────────
    # READ_ONLY refuses every administrative action regardless of the
    # user's real privileges. DBA still checks real privileges (via
    # Capabilities) before offering an action — see core/permissions.py.
    mode: str = "READ_ONLY"          # READ_ONLY | DBA

    @property
    def is_readonly(self) -> bool:
        return (self.mode or "").upper() != "DBA"

    def client_uri(self) -> str:
        """URI handed to MongoClient. Credentials are NOT embedded here when
        they came from discrete fields — they go as client kwargs instead
        (see client_kwargs), which keeps them out of any logged string."""
        if self.uri:
            return self.uri
        return f"mongodb://{self.host}:{self.port}/"

    def client_kwargs(self) -> dict:
        kw: dict = {
            "appname": self.app_name,
            "serverSelectionTimeoutMS": self.server_selection_timeout_ms,
            "connectTimeoutMS": self.connect_timeout_ms,
            "socketTimeoutMS": self.socket_timeout_ms,
            # Monitoring must not be the thing that exhausts a pool on the
            # monitored server — a handful of sockets is plenty.
            "maxPoolSize": 4,
        }
        if not self.uri:
            if self.direct:
                kw["directConnection"] = True
            if self.tls:
                kw["tls"] = True
            if self.user:
                kw["username"] = self.user
                kw["authSource"] = self.auth_db or "admin"
        else:
            if self.user:
                kw["username"] = self.user
        # With --uri the password may come from MDB_BRABO_PASSWORD so it never
        # shows up in `ps`; MongoClient kwargs override what's in the URI.
        # pymongo rejects a password without a username, hence the guard.
        if self.password and (self.user or _uri_has_user(self.uri)):
            kw["password"] = self.password
        return kw

    def display_target(self) -> str:
        """host:port (or the URI with the password masked) — safe to log/show."""
        if self.uri:
            return redact_uri(self.uri)
        return f"{self.host}:{self.port}"

    def history_key(self) -> str:
        return f"{self.host}:{self.port}" if not self.uri else redact_uri(self.uri)


def _uri_has_user(uri: str | None) -> bool:
    if not uri or "://" not in uri:
        return False
    authority = uri.split("://", 1)[1].split("/", 1)[0]
    return "@" in authority


def redact_uri(uri: str) -> str:
    """Mask the password in a mongodb:// URI (user:****@host)."""
    if "://" not in uri:
        return uri
    scheme, rest = uri.split("://", 1)
    authority, sep, tail = rest.partition("/")
    if "@" in authority:
        creds, host = authority.rsplit("@", 1)
        user = creds.split(":", 1)[0]
        authority = f"{user}:****@{host}"
    return f"{scheme}://{authority}{sep}{tail}"


def build_uri(host: str, port: int, user: str | None, password: str | None,
              auth_db: str = "admin", direct: bool = False) -> str:
    """Helper for docs/tests — the app itself prefers client kwargs."""
    auth = ""
    if user:
        auth = quote_plus(user)
        if password:
            auth += ":" + quote_plus(password)
        auth += "@"
    params = [f"authSource={auth_db}"] if user else []
    if direct:
        params.append("directConnection=true")
    q = ("?" + "&".join(params)) if params else ""
    return f"mongodb://{auth}{host}:{port}/{q}"
