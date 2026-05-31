"""Tests for compose_answer_node behaviour across routes.

These don't make LLM calls — they exercise compose_answer directly with
crafted state dicts so we can lock in the routing-aware response logic.
"""
from __future__ import annotations

from app.agent.nodes import compose_answer_node


def test_clarify_returns_route_reason_text() -> None:
    state = {
        "route": "clarify",
        "route_reason": "Which metric do you mean?",
    }
    out = compose_answer_node(state)
    assert out["answer"] == "Which metric do you mean?"


def test_refuse_returns_route_reason_text() -> None:
    state = {
        "route": "refuse",
        "route_reason": "I cannot forecast future values.",
    }
    out = compose_answer_node(state)
    assert out["answer"] == "I cannot forecast future values."


def test_sql_error_says_sql_failed() -> None:
    state = {
        "route": "sql",
        "error": "syntax error near 'FROM'",
        "sql": "SELECT FROM orders",
        "previous_attempts": [{"sql": "SELECT FROM orders", "error": "syntax error near 'FROM'"}],
    }
    out = compose_answer_node(state)
    # New phrasing: tells user how many attempts and the last error verbatim
    assert "tried" in out["answer"].lower()
    assert "syntax error" in out["answer"]
    assert "SELECT FROM orders" in out["answer"]


def test_chart_error_with_successful_sql_shows_data_not_sql_failure() -> None:
    """Regression: when chart-building fails (e.g., LLM rate limit), the
    answer must NOT claim SQL execution failed. The data is intact."""
    state = {
        "route": "viz",
        "sql": "SELECT month, revenue FROM orders",
        "columns": ["month", "revenue"],
        "rows": [{"month": "2017-01", "revenue": 100.0}, {"month": "2017-02", "revenue": 200.0}],
        "row_count": 2,
        "chart_error": "429 rate limit exceeded",
        # Note: NO "error" key — SQL was fine
    }
    out = compose_answer_node(state)
    assert "execution failed" not in out["answer"]  # the bug we fixed
    assert "couldn't build the chart" in out["answer"]
    assert "rate limit" in out["answer"]
    # Data should still be visible
    assert "2017-01" in out["answer"]
    assert "200" in out["answer"]


def test_viz_with_chart_spec_describes_chart() -> None:
    state = {
        "route": "viz",
        "columns": ["month", "revenue"],
        "rows": [{"month": "2017-01", "revenue": 100.0}],
        "row_count": 1,
        "chart_spec": {"data": [{"type": "scatter"}], "layout": {"title": "X"}},
    }
    out = compose_answer_node(state)
    assert "chart" in out["answer"].lower()
    assert "1 rows" in out["answer"]


def test_sql_scalar_result() -> None:
    state = {
        "route": "sql",
        "columns": ["n"],
        "rows": [{"n": 99441}],
        "row_count": 1,
    }
    out = compose_answer_node(state)
    assert out["answer"] == "n: 99,441"


def test_sql_empty_result() -> None:
    state = {
        "route": "sql",
        "columns": ["x"],
        "rows": [],
        "row_count": 0,
    }
    out = compose_answer_node(state)
    assert out["answer"] == "The query returned no rows."


def test_successful_answer_after_one_correction_includes_footnote() -> None:
    state = {
        "route": "sql",
        "columns": ["category"],
        "rows": [{"category": "health_beauty"}],
        "row_count": 1,
        "previous_attempts": [
            {"sql": "SELECT bad_col FROM orders", "error": "Binder Error"},
        ],
    }
    out = compose_answer_node(state)
    assert "health_beauty" in out["answer"]
    assert "self-correction" in out["answer"].lower()
    assert "1" in out["answer"]


def test_successful_answer_after_two_corrections_says_corrections_plural() -> None:
    state = {
        "route": "sql",
        "columns": ["n"],
        "rows": [{"n": 5}],
        "row_count": 1,
        "previous_attempts": [
            {"sql": "x", "error": "e"},
            {"sql": "y", "error": "e"},
        ],
    }
    out = compose_answer_node(state)
    assert "self-corrections" in out["answer"].lower()


def test_no_corrections_no_footnote() -> None:
    state = {
        "route": "sql",
        "columns": ["n"],
        "rows": [{"n": 99441}],
        "row_count": 1,
        "previous_attempts": [],
    }
    out = compose_answer_node(state)
    assert "self-correction" not in out["answer"].lower()
