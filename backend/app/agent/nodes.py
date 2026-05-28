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
import re
from typing import Any

from langchain_core.messages import HumanMessage, SystemMessage
from langchain_google_genai import ChatGoogleGenerativeAI

from app.agent.state import AgentState
from app.config import settings
from app.tools.sql import get_sql_tool

logger = logging.getLogger(__name__)

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
    # Convert to plain JSON-serializable rows for the API
    rows = df.head(50).to_dict(orient="records")
    return {
        "columns": result.columns,
        "rows": rows,
        "row_count": len(df),
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
        return {"answer": f"{cols[0]}: {value}"}

    # Small table — render as compact text
    if row_count <= 10:
        header = " | ".join(cols)
        body = "\n".join(" | ".join(str(r[c]) for c in cols) for r in rows)
        return {"answer": f"{header}\n{body}"}

    # Larger result — show first 10 and total count
    header = " | ".join(cols)
    body = "\n".join(" | ".join(str(r[c]) for c in cols) for r in rows[:10])
    return {
        "answer": (
            f"{row_count} rows returned. First 10:\n{header}\n{body}"
        )
    }
