"""Graph nodes for the agent.

After session 3:
  router_node       — picks a route (sql / viz / clarify / refuse)
  write_sql_node    — LLM (Gemini) writes a single DuckDB query
  execute_sql_node  — runs the query, captures error if any
  make_chart_node   — turns a SQL result into a Plotly chart spec (viz route)
  clarify_node      — surfaces the router's clarifying question
  refuse_node       — surfaces the router's refusal explanation
  compose_answer    — composes the final user-facing answer

Self-correction is NOT in this version; failures pass through to the
response with the error message attached.
"""
from __future__ import annotations

import logging
import math
import re
from typing import Any

import pandas as pd
from langchain_core.messages import HumanMessage, SystemMessage

from app.agent.state import AgentState
from app.llm import get_chat_llm
from app.tools.sql import get_sql_tool

logger = logging.getLogger(__name__)

# Number of decimal places to retain on floating-point results. Floats from
# aggregations like SUM accumulate microscopic precision noise; trimming to
# 3dp keeps results meaningful for currency and ratios while removing
# artifacts like 1149781.8199999975.
NUMERIC_DECIMALS = 3

def _clean_dataframe(df: pd.DataFrame, decimals: int = NUMERIC_DECIMALS) -> pd.DataFrame:
    """Round float columns to remove aggregation noise.

    Float aggregations like SUM accumulate microscopic precision artifacts
    (e.g., 1149781.8199999975). Olist currency data has at most 2 decimals
    of real signal, so rounding to 3 leaves headroom for ratios and averages
    while keeping output clean.

    NaN values are NOT converted here — that has to happen at the dict level
    because pandas can't hold None inside a float64 column.
    """
    if df.empty:
        return df

    df = df.copy()
    float_cols = df.select_dtypes(include=["float", "float64", "float32"]).columns
    if len(float_cols):
        df[float_cols] = df[float_cols].round(decimals)
    return df


def _sanitize_records(records: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Replace NaN with None in serialized rows so JSON encoding works.

    Run this AFTER ``df.to_dict(orient="records")`` — at that point we have
    plain Python objects and can swap floats for None safely.
    """
    for row in records:
        for key, value in row.items():
            if isinstance(value, float) and math.isnan(value):
                row[key] = None
    return records


def _fmt_cell(value: Any) -> str:
    """Human-friendly cell format used in the answer string.

    - Integers: thousand-separated, no decimals
    - Floats:   thousand-separated, up to NUMERIC_DECIMALS decimals, trailing zeros stripped
    - None/NaN: empty string
    - Other:    str()
    """
    if value is None:
        return ""
    if isinstance(value, bool):
        return str(value)
    if isinstance(value, int):
        return f"{value:,}"
    if isinstance(value, float):
        if math.isnan(value):
            return ""
        rounded = round(value, NUMERIC_DECIMALS)
        if rounded == int(rounded):
            return f"{int(rounded):,}"
        s = f"{rounded:,.{NUMERIC_DECIMALS}f}".rstrip("0").rstrip(".")
        return s
    return str(value)


# --- LLM client (singleton) -------------------------------------------------
_llm: Any = None


def _get_llm():
    global _llm
    if _llm is None:
        _llm = get_chat_llm()
    return _llm


# --- Prompt for SQL generation ---------------------------------------------
SQL_SYSTEM_PROMPT = """You are an expert data analyst who writes DuckDB SQL.

Rules:
- Output ONLY a single SQL statement. No prose, no markdown fences, no comments.
- Use the schema provided. Do not invent columns.
- Prefer concise queries. Use COUNT(DISTINCT order_id) when counting orders,
  because the table has one row per order item.
- For text matching on categorical columns, prefer = over LIKE unless the
  question implies fuzzy match.
- For "top N" questions, include ORDER BY ... DESC and LIMIT N.
- For TIME-SERIES questions (anything per day/week/month/year), produce a
  SINGLE date or timestamp column using DATE_TRUNC('month', ts) or
  STRFTIME('%Y-%m', ts). Do NOT split year and month into separate columns —
  that makes downstream charts collapse points across years.
- Never write INSERT, UPDATE, DELETE, CREATE, DROP, or ALTER statements.
"""


_FENCE_RE = re.compile(r"^```(?:sql)?\s*|\s*```$", re.IGNORECASE | re.MULTILINE)


def _strip_fences(text: str) -> str:
    """Remove markdown code fences if the model added them despite instructions."""
    return _FENCE_RE.sub("", text).strip()


# --- Nodes -----------------------------------------------------------------


def write_sql_node(state: AgentState) -> dict[str, Any]:
    question = state["question"]
    schema = state["schema"]

    llm = _get_llm()
    prompt = (
        f"Schema:\n{schema}\n\n"
        f"Question: {question}\n\n"
        "Write the SQL."
    )

    response = llm.invoke([
        SystemMessage(content=SQL_SYSTEM_PROMPT),
        HumanMessage(content=prompt),
    ])
    sql = _strip_fences(response.content if isinstance(response.content, str) else str(response.content))
    logger.info("Generated SQL: %s", sql)
    return {"sql": sql}


def execute_sql_node(state: AgentState) -> dict[str, Any]:
    sql = state.get("sql", "")
    tool = get_sql_tool()
    result = tool.execute(sql)

    if not result.ok:
        logger.warning("SQL execution failed: %s", result.error)
        return {"error": result.error or "unknown error", "rows": [], "columns": [], "row_count": 0}

    df = result.dataframe
    assert df is not None  # narrowed by result.ok
    cleaned = _clean_dataframe(df)
    rows = _sanitize_records(cleaned.head(50).to_dict(orient="records"))
    return {
        "columns": result.columns,
        "rows": rows,
        "row_count": len(cleaned),
        "error": "",  # explicitly clear any prior error in state
    }


def compose_answer_node(state: AgentState) -> dict[str, Any]:
    """Format a short user-facing answer.

    Branches on `state["route"]` so each path gets an appropriate phrasing.
    Rule-based for cost/latency reasons; the LLM has already done its work
    upstream.
    """
    route = state.get("route", "sql")

    # Clarify and refuse routes already wrote their text into route_reason.
    if route == "clarify":
        return {"answer": state.get("route_reason", "Could you clarify your question?")}

    if route == "refuse":
        return {"answer": state.get("route_reason", "I can't answer that with this data.")}

    # SQL or viz with an SQL execution error
    if state.get("error"):
        return {
            "answer": (
                f"I tried to answer with SQL but execution failed: {state['error']}\n\n"
                f"Generated SQL was:\n{state.get('sql', '')}"
            )
        }

    rows = state.get("rows", [])
    cols = state.get("columns", [])
    row_count = state.get("row_count", 0)
    chart_error = state.get("chart_error")

    # Viz route, SQL succeeded, chart succeeded
    if route == "viz" and state.get("chart_spec"):
        return {
            "answer": (
                f"Here is the chart for your question. "
                f"It is built from {row_count} rows aggregated by SQL "
                f"(columns: {cols})."
            )
        }

    # Viz route, SQL succeeded but chart-building failed
    # (e.g., LLM rate limit during chart_spec call)
    # Show the data anyway and explain the chart problem honestly.
    if route == "viz" and chart_error:
        preview = _render_table_preview(rows, cols, row_count)
        return {
            "answer": (
                f"I aggregated the data but couldn't build the chart: {chart_error}\n\n"
                f"Here are the results as a table:\n{preview}"
            )
        }

    if row_count == 0:
        return {"answer": "The query returned no rows."}

    if row_count == 1 and len(cols) == 1:
        # Single scalar — most common for COUNT/SUM/AVG questions
        value = rows[0][cols[0]]
        return {"answer": f"{cols[0]}: {_fmt_cell(value)}"}

    return {"answer": _render_table_preview(rows, cols, row_count)}


def _render_table_preview(
    rows: list[dict[str, Any]],
    cols: list[str],
    row_count: int,
    max_rows: int = 10,
) -> str:
    """Compact text rendering of a tabular result.

    Used both for normal SQL answers and as a fallback when chart-building
    fails on the viz path.
    """
    if not rows:
        return "(no rows)"
    header = " | ".join(cols)
    if row_count <= max_rows:
        body = "\n".join(" | ".join(_fmt_cell(r[c]) for c in cols) for r in rows)
        return f"{header}\n{body}"
    body = "\n".join(" | ".join(_fmt_cell(r[c]) for c in cols) for r in rows[:max_rows])
    return f"{row_count} rows returned. First {max_rows}:\n{header}\n{body}"


def make_chart_node(state: AgentState) -> dict[str, Any]:
    """Build a Plotly chart spec from the executed SQL result.

    Only invoked on the `viz` route after a successful SQL execution.
    Failures here record a `chart_error` (NOT `error`, which means SQL
    failed) so the data can still be returned to the user with a note.
    """
    from app.tools.viz import build_plotly_spec, choose_chart

    rows = state.get("rows", [])
    cols = state.get("columns", [])
    if not rows or not cols:
        return {"chart_spec": {}}

    try:
        spec = choose_chart(
            question=state["question"],
            columns=cols,
            sample_rows=rows,
        )
        plotly_dict = build_plotly_spec(spec, rows)
    except Exception as e:  # noqa: BLE001 — log and degrade gracefully
        logger.warning("Chart build failed: %s", e)
        return {"chart_spec": {}, "chart_error": str(e)}

    return {"chart_spec": plotly_dict}


def clarify_node(state: AgentState) -> dict[str, Any]:
    """No-op pass-through; clarifying text already in route_reason.

    Exists as a distinct node so traces clearly show why no SQL ran.
    """
    return {}


def refuse_node(state: AgentState) -> dict[str, Any]:
    """No-op pass-through; refusal text already in route_reason."""
    return {}
