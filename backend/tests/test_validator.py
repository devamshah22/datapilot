"""Tests for the rule-based validator.

The validator is pure (no LLM, no I/O), so we exercise it directly with
crafted state dicts.
"""
from __future__ import annotations

from app.agent.validator import validator_node


def _ok_state(question: str, rows: list, cols: list) -> dict:
    return {
        "question": question,
        "rows": rows,
        "columns": cols,
        "row_count": len(rows),
    }


# --- pass-through cases -----------------------------------------------------


def test_passes_when_sql_already_failed() -> None:
    state = {"error": "syntax error", "question": "top 5 customers"}
    out = validator_node(state)
    assert out["validation_failure"] == ""


def test_passes_with_normal_result() -> None:
    state = _ok_state(
        question="Which product category has the highest revenue?",
        rows=[{"category": "x", "revenue": 100.0}],
        cols=["category", "revenue"],
    )
    out = validator_node(state)
    assert out["validation_failure"] == ""


def test_passes_zero_rows_when_question_does_not_imply_rows() -> None:
    """Zero rows are perfectly fine for some questions."""
    state = _ok_state(
        question="List orders cancelled before they shipped",
        rows=[],
        cols=["order_id"],
    )
    out = validator_node(state)
    assert out["validation_failure"] == ""


# --- failure cases ----------------------------------------------------------


def test_flags_zero_rows_when_question_implies_top_n() -> None:
    state = _ok_state(
        question="Top 5 customers by total spend",
        rows=[],
        cols=["customer", "spend"],
    )
    out = validator_node(state)
    assert out["validation_failure"]
    assert "zero rows" in out["validation_failure"].lower()


def test_flags_zero_rows_when_question_says_highest() -> None:
    state = _ok_state(
        question="Which category has the highest revenue?",
        rows=[],
        cols=["category"],
    )
    out = validator_node(state)
    assert out["validation_failure"]


def test_flags_all_null_single_column_result() -> None:
    state = _ok_state(
        question="What is the average review score?",
        rows=[{"avg": None}],
        cols=["avg"],
    )
    out = validator_node(state)
    assert out["validation_failure"]
    assert "null" in out["validation_failure"].lower()
