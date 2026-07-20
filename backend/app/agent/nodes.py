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
    """Replace NaN with None and convert non-JSON-serializable types in serialized rows.

    Run this AFTER ``df.to_dict(orient="records")`` — at that point we have
    plain Python objects and can swap floats for None safely.
    """
    import datetime as _dt

    for row in records:
        for key, value in row.items():
            if isinstance(value, float) and math.isnan(value):
                row[key] = None
            elif hasattr(value, "isoformat"):
                # pandas Timestamp, datetime, date → ISO string
                row[key] = value.isoformat()
            elif isinstance(value, _dt.timedelta):
                row[key] = str(value)
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
- HUMAN-READABLE COLUMNS: When two columns hold the same information,
  ALWAYS use the English / human-readable one in the SELECT list, NOT the
  native or coded one. Concrete example for this dataset:
    BAD:  SELECT product_category_name FROM orders GROUP BY product_category_name
    GOOD: SELECT product_category_en   FROM orders GROUP BY product_category_en
  The user does not read Portuguese. Use product_category_en, not product_category_name.

FOLLOW-UPS: When the prompt includes "PREVIOUS QUERIES IN THIS SESSION",
the user's current question may be a refinement of an earlier one. In
that case, MODIFY the previous SQL rather than writing a new one from
scratch — keep its SELECT shape, its WHERE filters, and its metric.

Concrete follow-up examples. Assume the previous query was:
    SELECT product_category_en
    FROM orders
    GROUP BY product_category_en
    ORDER BY SUM(price + freight_value) DESC
    LIMIT 1
which returned product_category_en = 'health_beauty'.

  Follow-up: "Now break that down by Brazilian state"
    BAD:  SELECT customer_state, COUNT(DISTINCT order_id) FROM orders
          GROUP BY customer_state
          (drops the revenue metric AND the category filter — wrong)
    GOOD: SELECT customer_state, SUM(price + freight_value) AS revenue
          FROM orders
          WHERE product_category_en = 'health_beauty'
          GROUP BY customer_state
          ORDER BY revenue DESC

  Follow-up: "Only the last 6 months"
    BAD:  rewrites from scratch, drops the existing GROUP BY
    GOOD: same SELECT and GROUP BY, ADD a WHERE clause on
          order_purchase_timestamp

  Follow-up: "Make it a chart" / "Show me a bar chart of that"
    GOOD: produce essentially the SAME SQL as before — the chart-vs-table
          decision is handled elsewhere; your job is the data.

- Never write INSERT, UPDATE, DELETE, CREATE, DROP, or ALTER statements.
"""


_FENCE_RE = re.compile(r"^```(?:sql)?\s*|\s*```$", re.IGNORECASE | re.MULTILINE)


def _strip_fences(text: str) -> str:
    """Remove markdown code fences if the model added them despite instructions."""
    return _FENCE_RE.sub("", text).strip()


# --- Helpers for error composition ------------------------------------------


def _compose_error_explanation(state: AgentState) -> str:
    """Ask the LLM to explain what went wrong in a user-friendly way.

    The LLM has full context: the question, the schema (what data exists),
    and the error. It can generate a much better explanation than any
    hardcoded message — e.g., 'Your file doesn't have an orders column.
    The available columns are: Product, Price, Category. Try asking about those.'
    """
    question = state.get("question", "")
    schema = state.get("schema", "")
    error = state.get("error", "") or state.get("validation_failure", "")

    llm = _get_llm()
    prompt = (
        f"The user asked: \"{question}\"\n\n"
        f"Available data schema:\n{schema}\n\n"
        f"The query failed with: {error}\n\n"
        "Write a SHORT (1-3 sentences), friendly response explaining to the user "
        "why their question couldn't be answered. Be specific — mention what "
        "columns or data actually exist so they can rephrase. Do NOT expose "
        "technical SQL errors, table names, or internal details. "
        "Do NOT apologize excessively. Just be helpful and direct."
    )

    try:
        response = llm.invoke([
            SystemMessage(content="You are a helpful data assistant explaining to a user why their question couldn't be answered with their uploaded data."),
            HumanMessage(content=prompt),
        ])
        return response.content if isinstance(response.content, str) else str(response.content)
    except Exception:
        # If LLM call itself fails, fall back to a simple generic message
        return "I couldn't answer that question with your current data. Try rephrasing, or check that the right file is uploaded."


# --- Nodes -----------------------------------------------------------------


def write_sql_node(state: AgentState) -> dict[str, Any]:
    question = state["question"]
    schema = state["schema"]
    session_context = state.get("session_context", "")
    previous_attempts = state.get("previous_attempts", [])

    llm = _get_llm()

    if previous_attempts:
        # We're in a self-correction retry. Tell the LLM exactly what
        # was tried, what went wrong, and instruct it to fix.
        history = "\n\n".join(
            f"Attempt {i + 1}:\nSQL:\n{a['sql']}\nError: {a['error']}"
            for i, a in enumerate(previous_attempts)
        )
        prompt = (
            f"Schema:\n{schema}\n\n"
            f"Question: {question}\n\n"
            f"PREVIOUS ATTEMPTS THAT FAILED:\n{history}\n\n"
            "Write a CORRECTED SQL query that fixes the error(s) above. "
            "Do not repeat any of the previous queries — diagnose what went "
            "wrong (wrong column? missing cast? bad filter?) and address it."
        )
    else:
        # Normal path. Include session context if this might be a follow-up.
        if session_context:
            prompt = (
                f"Schema:\n{schema}\n\n"
                f"{session_context}\n\n"
                f"Current question: {question}\n\n"
                "Write the SQL. If this question refers to a previous query "
                "(e.g., 'that', 'now break that down by ...', 'only the last "
                "6 months'), build on that previous query rather than "
                "ignoring it."
            )
        else:
            prompt = (
                f"Schema:\n{schema}\n\n"
                f"Question: {question}\n\n"
                "Write the SQL."
            )

    response = llm.invoke([
        SystemMessage(content=SQL_SYSTEM_PROMPT),
        HumanMessage(content=prompt),
    ])
    sql = _strip_fences(
        response.content if isinstance(response.content, str) else str(response.content)
    )
    logger.info(
        "Generated SQL (attempt %d): %s",
        len(previous_attempts) + 1, sql,
    )
    return {"sql": sql}


def execute_sql_node(state: AgentState) -> dict[str, Any]:
    sql = state.get("sql", "")
    session_id = state.get("session_id", "")

    # Pick the right executor: per-session uploads or global Olist fallback
    from app.tools.dataset_manager import get_dataset_manager
    mgr = get_dataset_manager()
    ds = mgr.get(session_id) if session_id else None

    if ds and ds.files:
        # Execute against the session's uploaded Parquet data
        sql_stripped = sql.strip().rstrip(";").strip()
        first_token = sql_stripped.split(None, 1)[0].lower() if sql_stripped else ""
        if first_token not in {"select", "with"}:
            error_msg = f"Only SELECT/WITH queries are allowed; got '{first_token or '<empty>'}'."
            attempts = list(state.get("previous_attempts", []))
            attempts.append({"sql": sql, "error": error_msg})
            return {"error": error_msg, "rows": [], "columns": [], "row_count": 0, "previous_attempts": attempts}

        try:
            rel = ds.execute(sql_stripped)
            df = rel.fetch_df()
        except Exception as e:
            error_msg = f"{type(e).__name__}: {e}"
            logger.warning("SQL execution failed: %s", error_msg)
            attempts = list(state.get("previous_attempts", []))
            attempts.append({"sql": sql, "error": error_msg})
            return {"error": error_msg, "rows": [], "columns": [], "row_count": 0, "previous_attempts": attempts}

        cleaned = _clean_dataframe(df)
        rows = _sanitize_records(cleaned.head(50).to_dict(orient="records"))
        return {
            "columns": list(df.columns),
            "rows": rows,
            "row_count": len(cleaned),
            "error": "",
        }
    else:
        # No data uploaded — shouldn't reach here (router should clarify)
        return {
            "error": "No data available. Please upload a CSV or Excel file first.",
            "rows": [],
            "columns": [],
            "row_count": 0,
        }


def compose_answer_node(state: AgentState) -> dict[str, Any]:
    """Format a short user-facing answer.

    Branches on `state["route"]` so each path gets an appropriate phrasing.
    Rule-based for cost/latency reasons; the LLM has already done its work
    upstream.

    The user-facing answer text intentionally does NOT mention how many
    self-correction retries happened. End users care about the answer, not
    the agent's internal reliability process. Retry data is still surfaced
    in the API response (`previous_attempts`, `retry_count`) for devs.
    """
    route = state.get("route", "sql")

    # Clarify and refuse routes already wrote their text into route_reason.
    if route == "clarify":
        return {"answer": state.get("route_reason", "Could you clarify your question?")}

    if route == "refuse":
        return {"answer": state.get("route_reason", "I can't answer that with this data.")}

    # Chat route: answer already set by chat_node
    if route == "chat":
        return {"answer": state.get("answer", "")}

    # SQL or viz with an SQL execution error after retries exhausted.
    # We still tell the user the agent failed, but we don't quote the raw
    # SQL or count the retries — that's debug info, surfaced via the API
    # `previous_attempts` field instead.
    if state.get("error"):
        # Let the LLM compose a helpful, context-aware error explanation
        return {"answer": _compose_error_explanation(state)}

    # Validation failure with retries exhausted.
    if state.get("validation_failure"):
        return {"answer": _compose_error_explanation(state)}

    # Python route — answer comes from python_output
    if route == "python":
        python_output = state.get("python_output", "")
        if python_output:
            return {"answer": python_output}
        return {"answer": "The Python analysis ran but produced no output."}

    rows = state.get("rows", [])
    cols = state.get("columns", [])
    row_count = state.get("row_count", 0)
    chart_error = state.get("chart_error")

    # Viz route, SQL succeeded, chart succeeded
    if route == "viz" and state.get("chart_spec"):
        return {"answer": ""}

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


def chat_node(state: AgentState) -> dict[str, Any]:
    """Direct LLM response without any tool execution.

    Used for general questions, summaries, explanations, suggestions —
    anything where the LLM's intelligence is sufficient without running
    code against the data.
    """
    question = state["question"]
    schema = state.get("schema", "")
    session_context = state.get("session_context", "")

    llm = _get_llm()
    prompt = ""
    if schema:
        prompt += f"Available data:\n{schema}\n\n"
    if session_context:
        prompt += f"{session_context}\n\n"
    prompt += f"User: {question}"

    response = llm.invoke([
        SystemMessage(content=(
            "You are DataPilot, a friendly data analysis assistant. "
            "Answer the user's question using your knowledge and the data schema provided. "
            "Be concise, helpful, and direct. If the user asks what they can do, "
            "suggest specific questions based on the columns in their data. "
            "Always respond in English."
        )),
        HumanMessage(content=prompt),
    ])
    answer = response.content if isinstance(response.content, str) else str(response.content)
    return {"answer": answer}


# --- Python tool nodes -------------------------------------------------------

PYTHON_SYSTEM_PROMPT = """You are an expert data scientist who writes Python/pandas code.

Rules:
- Output ONLY executable Python code. No prose, no markdown fences, no comments explaining what the code does.
- The DataFrame is pre-loaded as `df`. Do NOT read files yourself.
- Store your final answer in a variable called `result`.
  - If the result is a number, assign it directly: result = 0.42
  - If the result is a DataFrame or Series, assign it: result = df_summary
  - If the result is text, assign a string: result = "The outliers are..."
- You have access to: pandas (pd), numpy (np), scipy.stats, sklearn.cluster,
  sklearn.preprocessing, sklearn.metrics, statistics, math, datetime.
- Do NOT import os, sys, subprocess, requests, urllib, or any I/O modules.
- Keep code concise. Prefer vectorized pandas/numpy over loops.
- For outlier detection, use IQR or z-score and explain the threshold in the result.
- For correlations, use df[col1].corr(df[col2]).
- For rolling averages, group by date first, then use .rolling().mean().
- For clustering, standardize features first, then use KMeans.
"""

_PYTHON_FENCE_RE = re.compile(r"^```(?:python)?\s*|\s*```$", re.IGNORECASE | re.MULTILINE)


def write_python_node(state: AgentState) -> dict[str, Any]:
    """LLM writes Python/pandas code for the question."""
    question = state["question"]
    schema = state["schema"]
    session_context = state.get("session_context", "")

    llm = _get_llm()
    prompt = f"Schema:\n{schema}\n\n"
    if session_context:
        prompt += f"{session_context}\n\n"
    prompt += f"Question: {question}\n\nWrite the Python code."

    response = llm.invoke([
        SystemMessage(content=PYTHON_SYSTEM_PROMPT),
        HumanMessage(content=prompt),
    ])
    code = response.content if isinstance(response.content, str) else str(response.content)
    code = _PYTHON_FENCE_RE.sub("", code).strip()
    logger.info("Generated Python code:\n%s", code)
    return {"python_code": code}


def execute_python_node(state: AgentState) -> dict[str, Any]:
    """Execute the LLM-generated Python code in a Docker sandbox."""
    from app.tools.python_tool import execute_python

    code = state.get("python_code", "")
    session_id = state.get("session_id", "")

    # Determine which parquet files to mount
    parquet_paths: list[str] = []

    # Check for per-session uploaded data first
    from app.tools.dataset_manager import get_dataset_manager
    mgr = get_dataset_manager()
    ds = mgr.get(session_id) if session_id else None
    if ds and ds.files:
        parquet_paths = [str(f.parquet_path.resolve()) for f in ds.files]
    else:
        # Fallback to Olist parquet (create if needed)
        from pathlib import Path
        from app.config import settings
        parquet = settings.dataset_path.with_suffix(".parquet")
        if not parquet.exists():
            import pandas as pd
            df = pd.read_csv(settings.dataset_path)
            df.to_parquet(parquet, index=False)
        parquet_paths = [str(parquet.resolve())]

    result = execute_python(code, parquet_paths=parquet_paths)

    if not result.ok:
        logger.warning("Python execution failed: %s", result.error)
        return {"error": result.error or "Python execution failed", "python_output": ""}

    return {"python_output": result.output or "", "error": ""}
