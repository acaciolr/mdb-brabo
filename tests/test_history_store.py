import time

from mongodb_brabo.core.history_store import HistoryStore


def test_record_query_and_projection(tmp_path):
    st = HistoryStore(tmp_path / "h.db")
    now = time.time()
    st.record("k", {"a": 1.0, "skip": None}, ts=now - 3 * 86400)
    st.record("k", {"a": 3.0}, ts=now - 86400)
    assert st.available_metrics("k") == ["a"]
    assert [v for _, v in st.query("k", "a", 0)] == [1.0, 3.0]
    p = st.growth_projection("k", "a")
    assert round(p["slope_per_day"], 3) == 1.0


def test_projection_needs_a_day(tmp_path):
    st = HistoryStore(tmp_path / "h.db")
    st.record("k", {"a": 1.0}, ts=time.time() - 60)
    st.record("k", {"a": 2.0})
    assert st.growth_projection("k", "a") == {}
