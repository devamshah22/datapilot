"""Tests for the answer formatter helpers."""
from __future__ import annotations

import pandas as pd

from app.agent.nodes import _clean_dataframe, _fmt_cell, _sanitize_records


# --- _fmt_cell --------------------------------------------------------------


def test_fmt_int_with_thousands_separator() -> None:
    assert _fmt_cell(99441) == "99,441"


def test_fmt_strips_float_noise_to_3_decimals() -> None:
    assert _fmt_cell(1149781.8199999975) == "1,149,781.82"


def test_fmt_drops_trailing_zeros() -> None:
    assert _fmt_cell(354.75) == "354.75"
    assert _fmt_cell(354.700) == "354.7"


def test_fmt_treats_whole_floats_as_int() -> None:
    assert _fmt_cell(1000.0) == "1,000"


def test_fmt_handles_none_and_nan() -> None:
    assert _fmt_cell(None) == ""
    assert _fmt_cell(float("nan")) == ""


def test_fmt_strings_pass_through() -> None:
    assert _fmt_cell("health_beauty") == "health_beauty"


# --- _clean_dataframe -------------------------------------------------------


def test_clean_rounds_floats_to_3dp() -> None:
    df = pd.DataFrame({"x": [1.123456789, 2.0]})
    out = _clean_dataframe(df)
    assert out["x"].tolist() == [1.123, 2.0]


def test_clean_handles_empty_frame() -> None:
    df = pd.DataFrame({"x": []})
    out = _clean_dataframe(df)
    assert out.empty


# --- _sanitize_records ------------------------------------------------------


def test_sanitize_replaces_nan_with_none() -> None:
    records = [{"a": 1.0, "b": float("nan")}, {"a": float("nan"), "b": 2.0}]
    out = _sanitize_records(records)
    assert out == [{"a": 1.0, "b": None}, {"a": None, "b": 2.0}]


def test_sanitize_leaves_other_values_alone() -> None:
    records = [{"a": "x", "b": 5, "c": None}]
    out = _sanitize_records(records)
    assert out == [{"a": "x", "b": 5, "c": None}]
