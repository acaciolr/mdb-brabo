"""
core/connection_manager.py
Thin wrapper around one pymongo.MongoClient per tab.

pymongo is synchronous. Every call made from the Textual event loop goes
through `run()`, which offloads it to a worker thread (asyncio.to_thread),
so a slow server can delay a panel's data but can never freeze the UI.
MongoClient itself is thread-safe and pools its sockets, so sharing one
client across concurrently-running collectors is the intended usage.

Read-only by construction: collectors only ever call `command()` with
read commands, `aggregate()` and `find()`. The ONLY method able to run an
administrative command is `execute_action()`, and it refuses outright when
the session is READ_ONLY.
"""

from __future__ import annotations

import asyncio
import logging
from typing import Any, Callable

from mongodb_brabo.core.config import AppConfig

log = logging.getLogger(__name__)


class ReadOnlyViolation(RuntimeError):
    """Raised when an administrative action is attempted in READ_ONLY mode."""


class ConnectionManager:
    def __init__(self, config: AppConfig) -> None:
        self.config = config
        self.client = None  # pymongo.MongoClient, created in connect()

    # ------------------------------------------------------------------
    # Lifecycle
    # ------------------------------------------------------------------

    def connect_sync(self) -> None:
        from pymongo import MongoClient

        self.client = MongoClient(self.config.client_uri(), **self.config.client_kwargs())
        # MongoClient is lazy — force a round-trip so bad credentials /
        # unreachable hosts fail here (with a clear message) instead of on
        # the first collector cycle.
        self.client.admin.command("ping")
        log.info("Connected to %s", self.config.display_target())

    async def connect(self) -> None:
        await asyncio.to_thread(self.connect_sync)

    def close_sync(self) -> None:
        if self.client is not None:
            try:
                self.client.close()
            except Exception:
                pass
            self.client = None

    async def close(self) -> None:
        await asyncio.to_thread(self.close_sync)

    # ------------------------------------------------------------------
    # Read helpers (sync — call them through run())
    # ------------------------------------------------------------------

    def command(self, db: str, cmd: str | dict, **kwargs) -> dict:
        """Run a read-only command. `cmd` may be a name or a full SON/dict."""
        return self.client[db].command(cmd, **kwargs)

    def aggregate(self, db: str, pipeline: list, collection: str | None = None,
                  limit: int | None = None) -> list[dict]:
        """Aggregate on a collection, or a db-level aggregate ($currentOp)
        when collection is None. Always carries maxTimeMS."""
        mt = self.config.query_max_time_ms
        if collection is None:
            cursor = self.client[db].aggregate(pipeline, maxTimeMS=mt)
        else:
            cursor = self.client[db][collection].aggregate(pipeline, maxTimeMS=mt)
        out: list[dict] = []
        for doc in cursor:
            out.append(doc)
            if limit is not None and len(out) >= limit:
                break
        cursor.close()
        return out

    def find(self, db: str, collection: str, flt: dict | None = None,
             projection: dict | None = None, sort: list | None = None,
             limit: int = 0) -> list[dict]:
        cur = self.client[db][collection].find(
            flt or {}, projection, sort=sort, limit=limit,
            max_time_ms=self.config.query_max_time_ms,
        )
        return list(cur)

    async def run(self, fn: Callable[..., Any], *args, **kwargs) -> Any:
        """Execute a blocking pymongo call off the event loop."""
        if self.client is None:
            raise RuntimeError("Not connected")
        return await asyncio.to_thread(fn, *args, **kwargs)

    # ------------------------------------------------------------------
    # Administrative actions — the single gate
    # ------------------------------------------------------------------

    def execute_action_sync(self, db: str, cmd: dict) -> tuple[bool, str]:
        if self.config.is_readonly:
            # Checked here, at the bottom of the stack, on purpose: even if a
            # panel forgets permissions.check_action(), nothing gets through.
            raise ReadOnlyViolation("Conexão em modo READ ONLY — ação administrativa recusada.")
        if self.client is None:
            return False, "Sem conexão"
        try:
            res = self.client[db].command(cmd)
            log.warning("ADMIN ACTION executed on %s db=%s cmd=%s ok=%s",
                        self.config.display_target(), db, list(cmd.keys())[0], res.get("ok"))
            return True, str(res.get("info") or res.get("msg") or "ok")
        except Exception as exc:
            # replSetStepDown legitimately drops our own connection — pymongo
            # surfaces that as AutoReconnect/NotPrimary. That's success.
            name = type(exc).__name__
            if "replSetStepDown" in cmd and name in ("AutoReconnect", "NotPrimaryError", "NetworkTimeout"):
                return True, "stepDown enviado (conexão reiniciada pelo servidor, esperado)"
            log.warning("ADMIN ACTION failed db=%s cmd=%s: %s", db, list(cmd.keys())[0], exc)
            return False, str(exc)

    async def execute_action(self, db: str, cmd: dict) -> tuple[bool, str]:
        if self.config.is_readonly:
            raise ReadOnlyViolation("Conexão em modo READ ONLY — ação administrativa recusada.")
        return await asyncio.to_thread(self.execute_action_sync, db, cmd)
