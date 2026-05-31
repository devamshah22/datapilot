"""Visualization tool: turn a tabular SQL result into a Plotly chart spec.

We do NOT render images server-side. Instead we emit a Plotly JSON spec
that the frontend (or any Plotly-compatible client) can render. This keeps
the backend stateless and chart-library-agnostic at the boundary.

The LLM picks chart type + axes via structured output; we then construct
the Plotly dict deterministically from the data so we don't trust the
model to emit valid JSON for the whole spec.
"""
from __future__ import annotations

import logging
from typing import Any, Literal

from langchain_core.messages import HumanMessage, SystemMessage
from pydantic import BaseModel, Field

from app.llm import get_structured_llm

logger = logging.getLogger(__name__)

ChartType = Literal["line", "bar", "histogram", "scatter", "pie"]


class ChartSpec(BaseModel):
    """LLM-produced chart description. We build the Plotly JSON from this."""

    chart_type: ChartType = Field(..., description="Best chart type for the data.")
    x_column: str = Field(..., description="Column to use on the x-axis (or labels for pie).")
    y_column: str | None = Field(
        default=None,
        description=(
            "Column to use on the y-axis (or values for pie). "
            "Leave null only for histograms where x is the value being binned."
        ),
    )
    title: str = Field(..., description="Short, human-readable chart title.")
    x_label: str = Field(..., description="X-axis label.")
    y_label: str = Field(..., description="Y-axis label.")


CHART_SYSTEM_PROMPT = """You decide how to visualize a tabular SQL result.

Given the user's question, the result columns, and a sample of rows,
choose the BEST chart type and the columns for each axis.

Guidelines:
- Time-series (date/timestamp on x, numeric on y) → "line"
- One categorical column + one numeric column → "bar"
- A single numeric column to study its distribution → "histogram"
  (in this case, x_column = the numeric column, y_column = null)
- Two numeric columns to study their relationship → "scatter"
- Small set of category shares (≤ 8 categories, parts-of-a-whole) → "pie"

Pick column names EXACTLY as shown. Title and labels should be concise."""


_chart_llm: Any = None


def _get_chart_llm():
    global _chart_llm
    if _chart_llm is None:
        _chart_llm = get_structured_llm(ChartSpec)
    return _chart_llm


def choose_chart(
    question: str,
    columns: list[str],
    sample_rows: list[dict[str, Any]],
) -> ChartSpec:
    """Ask the LLM to pick chart type + axes from the result data."""
    sample_str = "\n".join(str(row) for row in sample_rows[:5])
    prompt = (
        f"Question: {question}\n\n"
        f"Result columns: {columns}\n\n"
        f"Sample rows (up to 5):\n{sample_str}\n\n"
        "Choose the chart."
    )
    spec: ChartSpec = _get_chart_llm().invoke([
        SystemMessage(content=CHART_SYSTEM_PROMPT),
        HumanMessage(content=prompt),
    ])
    logger.info("Chart: %s on x=%s y=%s", spec.chart_type, spec.x_column, spec.y_column)
    return spec


def build_plotly_spec(
    spec: ChartSpec,
    rows: list[dict[str, Any]],
) -> dict[str, Any]:
    """Turn a ChartSpec + rows into a Plotly JSON dict.

    Output structure: ``{"data": [...], "layout": {...}}`` — directly
    consumable by Plotly.js, plotly.py, or `plotly.io.from_json`.
    """
    if not rows:
        return {
            "data": [],
            "layout": {"title": spec.title},
        }

    # Validate columns exist
    available = set(rows[0].keys())
    if spec.x_column not in available:
        raise ValueError(
            f"x_column '{spec.x_column}' not in result columns {sorted(available)}"
        )
    if spec.y_column is not None and spec.y_column not in available:
        raise ValueError(
            f"y_column '{spec.y_column}' not in result columns {sorted(available)}"
        )

    x_values = [row[spec.x_column] for row in rows]
    y_values = (
        [row[spec.y_column] for row in rows]
        if spec.y_column is not None
        else None
    )

    if spec.chart_type == "line":
        trace = {
            "type": "scatter",
            "mode": "lines+markers",
            "x": x_values,
            "y": y_values,
            "name": spec.y_label,
        }
    elif spec.chart_type == "bar":
        trace = {
            "type": "bar",
            "x": x_values,
            "y": y_values,
            "name": spec.y_label,
        }
    elif spec.chart_type == "histogram":
        trace = {
            "type": "histogram",
            "x": x_values,
            "name": spec.x_label,
        }
    elif spec.chart_type == "scatter":
        trace = {
            "type": "scatter",
            "mode": "markers",
            "x": x_values,
            "y": y_values,
            "name": spec.y_label,
        }
    elif spec.chart_type == "pie":
        trace = {
            "type": "pie",
            "labels": x_values,
            "values": y_values,
        }
    else:  # pragma: no cover — Literal already prevents this
        raise ValueError(f"Unsupported chart type: {spec.chart_type}")

    layout: dict[str, Any] = {"title": spec.title}
    if spec.chart_type != "pie":
        layout["xaxis"] = {"title": spec.x_label}
        layout["yaxis"] = {"title": spec.y_label}

    return {"data": [trace], "layout": layout}
