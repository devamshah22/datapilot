"""Unit tests for the in-session memory store.

Pure logic, no LLM calls — these run in milliseconds.
"""
from __future__ import annotations

import time

from app.session import (
    QueryMemory,
    SessionStore,
    render_session_context,
)


def _q(question: str = "Q") -> QueryMemory:
    return QueryMemory(
        question=question,
        sql="SELECT 1",
        columns=["x"],
        row_count=1,
        sample_rows=[{"x": 1}],
    )


def test_get_or_create_mints_id_when_none() -> None:
    store = SessionStore()
    s = store.get_or_create(None)
    assert s.session_id
    assert len(store) == 1


def test_get_or_create_returns_existing_session() -> None:
    store = SessionStore()
    s1 = store.get_or_create(None)
    s2 = store.get_or_create(s1.session_id)
    assert s1.session_id == s2.session_id
    assert len(store) == 1


def test_record_query_appends() -> None:
    store = SessionStore()
    s = store.get_or_create(None)
    store.record_query(s.session_id, _q("first"))
    store.record_query(s.session_id, _q("second"))
    s2 = store.get(s.session_id)
    assert s2 is not None
    assert [q.question for q in s2.recent_queries] == ["first", "second"]


def test_max_queries_evicts_oldest() -> None:
    store = SessionStore(max_queries_per_session=2)
    s = store.get_or_create(None)
    store.record_query(s.session_id, _q("a"))
    store.record_query(s.session_id, _q("b"))
    store.record_query(s.session_id, _q("c"))
    s2 = store.get(s.session_id)
    assert s2 is not None
    assert [q.question for q in s2.recent_queries] == ["b", "c"]


def test_ttl_evicts_idle_sessions() -> None:
    store = SessionStore(ttl_seconds=0.05)
    s = store.get_or_create(None)
    store.record_query(s.session_id, _q())
    time.sleep(0.1)
    # Triggering any access runs lazy cleanup
    assert store.get(s.session_id) is None
    assert len(store) == 0


def test_render_empty_context_is_empty_string() -> None:
    store = SessionStore()
    s = store.get_or_create(None)
    assert render_session_context(s) == ""


def test_render_context_lists_recent_queries() -> None:
    store = SessionStore()
    s = store.get_or_create(None)
    store.record_query(s.session_id, _q("Which category has highest revenue?"))
    s = store.get(s.session_id)
    assert s is not None
    out = render_session_context(s)
    assert "Which category has highest revenue?" in out
    assert "PREVIOUS QUERIES" in out
