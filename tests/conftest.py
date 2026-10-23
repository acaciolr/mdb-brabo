import os
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))


@pytest.fixture(autouse=True)
def isolated_home(tmp_path, monkeypatch):
    """Never touch the real ~/.mdb_brabo (history.db, log, connections.json)."""
    monkeypatch.setenv("MDB_BRABO_HOME", str(tmp_path / "home"))
    monkeypatch.delenv("MDB_BRABO_PASSWORD", raising=False)
    import mongodb_brabo.core.connection_session as cs
    monkeypatch.setattr(cs, "_shared_history", None)
    yield
