"""
core/paths.py
Single place that decides where MDB BRABO keeps its local state
(log, history.db, connections.json). MDB_BRABO_HOME overrides the default
~/.mdb_brabo — the tests use it so they never touch the real home dir.
"""
from __future__ import annotations

import os
from pathlib import Path


def brabo_home() -> Path:
    base = os.environ.get("MDB_BRABO_HOME")
    path = Path(base) if base else Path.home() / ".mdb_brabo"
    path.mkdir(parents=True, exist_ok=True)
    return path
