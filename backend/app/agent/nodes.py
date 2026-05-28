"""Graph nodes for the v1 agent.

Three nodes:
  1. write_sql        — LLM (Gemini) writes a single DuckDB query for the question
  2. execute_sql      — runs the query, captures error if any
  3. compose_answer   — turns the SQL result into a short user-facing answer

v1 has NO self-correction or tool routing. Failures pass through to the
response with the error message attached.
"""
from __future__ import annotations

import logging
import math
import re
from typing import Any

import pandas as pd
from langchain_core.messages import HumanMessage, SystemMessage
from langchain_google_genai import ChatGoogleGenerativeAI

from app.agent.state import AgentState
from app.config import settings
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
_llm: ChatGoogleGenerativeAI | None = None


def _get_llm() -> ChatGoogleGenerativeAI:
    global _llm
    if _llm is None:
        _llm = ChatGoogleGenerativeAI(
            model=settings.primary_model,
            google_api_key=settings.gemini_api_key,
            temperature=0.0,
        )
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
    """Format a short user-facing answer from the SQL result.

    v1 keeps this rule-based (no extra LLM call) to minimize cost and latency.
    A future version can use the LLM to summarize complex tables.
    """
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

    if row_count == 0:
        return {"answer": "The query returned no rows."}

    if row_count == 1 and len(cols) == 1:
        # Single scalar — most common for COUNT/SUM/AVG questions
        value = rows[0][cols[0]]
        return {"answer": f"{cols[0]}: {_fmt_cell(value)}"}

    # Small table — render as compact text
    if row_count <= 10:
        header = " | ".join(cols)
        body = "\n".join(" | ".join(_fmt_cell(r[c]) for c in cols) for r in rows)
        return {"answer": f"{header}\n{body}"}

    # Larger result — show first 10 and total count
    header = " | ".join(cols)
    body = "\n".join(" | ".join(_fmt_cell(r[c]) for c in cols) for r in rows[:10])
    return {
        "answer": (
            f"{row_count} rows returned. First 10:\n{header}\n{body}"
        )
    }
