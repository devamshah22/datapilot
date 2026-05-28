"""Tests for the visualization tool.

The chart-type selection uses the LLM and is exercised via integration tests
elsewhere; these unit tests focus on the deterministic spec-builder so we
can catch regressions without spending API calls.
"""
from __future__ import annotations

import pytest

from app.tools.viz import ChartSpec, build_plotly_spec


def _spec(**overrides) -> ChartSpec:
    base = {
        "chart_type": "line",
        "x_column": "month",
        "y_column": "revenue",
        "title": "Monthly revenue",
        "x_label": "Month",
        "y_label": "Revenue",
    }
    base.update(overrides)
    return ChartSpec(**base)


# --- happy paths ------------------------------------------------------------


def test_line_spec_has_lines_plus_markers() -> None:
    rows = [{"month": "2017-01", "revenue": 100}, {"month": "2017-02", "revenue": 200}]
    out = build_plotly_spec(_spec(), rows)
    assert out["data"][0]["type"] == "scatter"
    assert out["data"][0]["mode"] == "lines+markers"
    assert out["data"][0]["x"] == ["2017-01", "2017-02"]
    assert out["data"][0]["y"] == [100, 200]
    assert out["layout"]["title"] == "Monthly revenue"
    assert out["layout"]["xaxis"]["title"] == "Month"
    assert out["layout"]["yaxis"]["title"] == "Revenue"


def test_bar_spec() -> None:
    rows = [{"month": "Jan", "revenue": 1}, {"month": "Feb", "revenue": 2}]
    out = build_plotly_spec(_spec(chart_type="bar"), rows)
    assert out["data"][0]["type"] == "bar"


def test_histogram_uses_only_x() -> None:
    rows = [{"price": v} for v in [10, 12, 15, 20, 25, 30]]
    out = build_plotly_spec(
        _spec(chart_type="histogram", x_column="price", y_column=None,
              x_label="Price", y_label="Count"),
        rows,
    )
    assert out["data"][0]["type"] == "histogram"
    assert "x" in out["data"][0]
    assert "y" not in out["data"][0]


def test_pie_uses_labels_and_values() -> None:
    rows = [{"month": "Jan", "revenue": 30}, {"month": "Feb", "revenue": 70}]
    out = build_plotly_spec(_spec(chart_type="pie"), rows)
    assert out["data"][0]["type"] == "pie"
    assert out["data"][0]["labels"] == ["Jan", "Feb"]
    assert out["data"][0]["values"] == [30, 70]
    # Pie charts shouldn't have axis labels in layout
    assert "xaxis" not in out["layout"]
    assert "yaxis" not in out["layout"]


# --- error / edge cases -----------------------------------------------------


def test_empty_rows_returns_empty_data() -> None:
    out = build_plotly_spec(_spec(), [])
    assert out["data"] == []
    assert out["layout"]["title"] == "Monthly revenue"


def test_missing_x_column_raises() -> None:
    with pytest.raises(ValueError, match="x_column"):
        build_plotly_spec(_spec(x_column="not_there"), [{"month": "Jan", "revenue": 1}])


def test_missing_y_column_raises() -> None:
    with pytest.raises(ValueError, match="y_column"):
        build_plotly_spec(_spec(y_column="not_there"), [{"month": "Jan", "revenue": 1}])
