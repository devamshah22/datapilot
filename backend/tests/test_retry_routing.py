"""Tests for the retry-routing decision after the validator runs.

Pure function over state — no LLM or DuckDB calls needed.
"""
from __future__ import annotations

from app.agent.graph import decide_after_validator
from app.config import settings


def test_no_problem_sql_route_goes_to_compose_answer() -> None:
    state = {"route": "sql", "previous_attempts": []}
    assert decide_after_validator(state) == "compose_answer"


def test_no_problem_viz_route_goes_to_make_chart() -> None:
    state = {"route": "viz", "previous_attempts": []}
    assert decide_after_validator(state) == "make_chart"


def test_sql_error_with_retries_left_triggers_retry() -> None:
    state = {
        "route": "sql",
        "error": "Binder Error: column not found",
        "previous_attempts": [{"sql": "SELECT bad", "error": "..."}],
    }
    # 1 retry used, max default is 3 -> retry
    assert decide_after_validator(state) == "retry"


def test_validation_failure_triggers_retry() -> None:
    state = {
        "route": "sql",
        "validation_failure": "Query returned zero rows",
        "previous_attempts": [],  # 0 used, plenty of budget
    }
    assert decide_after_validator(state) == "retry"


def test_retry_budget_exhausted_falls_through_to_compose_answer() -> None:
    # max_agent_retries failed attempts already in state — give up
    state = {
        "route": "sql",
        "error": "still failing",
        "previous_attempts": [
            {"sql": "x", "error": "e"} for _ in range(settings.max_agent_retries)
        ],
    }
    assert decide_after_validator(state) == "compose_answer"


def test_viz_with_sql_error_goes_to_compose_answer_not_make_chart() -> None:
    """If SQL eventually failed, don't try to chart nonexistent data."""
    state = {
        "route": "viz",
        "error": "broken",
        "previous_attempts": [
            {"sql": "x", "error": "e"} for _ in range(settings.max_agent_retries)
        ],
    }
    assert decide_after_validator(state) == "compose_answer"
