"""LangGraph state machine for the agent.

Graph (after session 4):

                                                  ┌── retry (≤ N) ──┐
                                                  ▼                 │
    START → router ──────┬─→ write_sql → execute_sql → validator ─┐ │
                         │                                        │ │
                         │     ┌──────── invalid + retries left ──┘ │
                         │     │                                    │
                         │     └─────────────────────────────────────
                         │
                         ├─→ clarify_node ──────────────→ compose_answer → END
                         ├─→ refuse_node ───────────────→ compose_answer → END
                         │
                         └─→ (sql) ──valid──→ compose_answer → END
                             (viz) ──valid──→ make_chart → compose_answer → END

The validator is rule-based (no LLM call) so the retry decision is fast
and deterministic. Self-correction is bounded by settings.max_agent_retries.

Session 5 added in-session memory: ``run_agent`` accepts an optional
session id, loads recent queries as prompt context, and the caller
(API layer) records successful queries back into the SessionStore.
"""
from __future__ import annotations

from langgraph.graph import END, START, StateGraph

from app.agent.nodes import (
    clarify_node,
    compose_answer_node,
    execute_python_node,
    execute_sql_node,
    make_chart_node,
    refuse_node,
    write_python_node,
    write_sql_node,
)
from app.agent.router import decide_after_router, router_node
from app.agent.state import AgentState
from app.agent.validator import validator_node
from app.config import settings
from app.session import get_session_store, render_session_context


def decide_after_validator(state: AgentState) -> str:
    """Where to go after the validator.

    - If validation revealed a problem (SQL error OR result invalid) AND
      we still have retries: send back to write_sql with the failed
      attempt(s) in state for context.
    - Otherwise proceed to chart-building (viz) or compose_answer.
    """
    has_problem = bool(state.get("error")) or bool(state.get("validation_failure"))
    retries_used = len(state.get("previous_attempts", []))

    if has_problem and retries_used < settings.max_agent_retries:
        return "retry"

    if state.get("route") == "viz" and not state.get("error"):
        return "make_chart"

    return "compose_answer"


def build_graph():
    builder = StateGraph(AgentState)
    builder.add_node("router", router_node)
    builder.add_node("write_sql", write_sql_node)
    builder.add_node("execute_sql", execute_sql_node)
    builder.add_node("validator", validator_node)
    builder.add_node("make_chart", make_chart_node)
    builder.add_node("write_python", write_python_node)
    builder.add_node("execute_python", execute_python_node)
    builder.add_node("clarify_node", clarify_node)
    builder.add_node("refuse_node", refuse_node)
    builder.add_node("compose_answer", compose_answer_node)

    builder.add_edge(START, "router")

    # After router: branch on chosen route
    builder.add_conditional_edges(
        "router",
        decide_after_router,
        {
            "sql": "write_sql",
            "viz": "write_sql",
            "python": "write_python",
            "clarify": "clarify_node",
            "refuse": "refuse_node",
        },
    )

    # SQL path: write -> execute -> validate -> (retry | make_chart | compose)
    builder.add_edge("write_sql", "execute_sql")
    builder.add_edge("execute_sql", "validator")
    builder.add_conditional_edges(
        "validator",
        decide_after_validator,
        {
            "retry": "write_sql",
            "make_chart": "make_chart",
            "compose_answer": "compose_answer",
        },
    )
    builder.add_edge("make_chart", "compose_answer")

    # Python path: write -> execute -> compose
    builder.add_edge("write_python", "execute_python")
    builder.add_edge("execute_python", "compose_answer")

    # Clarify / refuse paths bypass everything
    builder.add_edge("clarify_node", "compose_answer")
    builder.add_edge("refuse_node", "compose_answer")

    builder.add_edge("compose_answer", END)
    return builder.compile()


def run_agent(question: str, session_id: str | None = None) -> tuple[AgentState, str]:
    """Run the agent and return ``(final_state, session_id)``.

    Schema source priority:
      1. If the session has uploaded files → use per-session DatasetManager schema
      2. Otherwise → fall back to the global Olist dev dataset (SQLTool singleton)

    The session id used (existing or freshly minted) is returned so the
    caller can echo it back to the client.
    """
    graph = _get_graph()

    store = get_session_store()
    session = store.get_or_create(session_id)
    context = render_session_context(session)

    # Decide which schema + executor to use
    from app.tools.dataset_manager import get_dataset_manager
    mgr = get_dataset_manager()
    ds = mgr.get(session.session_id)

    if ds and ds.files:
        # User has uploaded data — use their schema
        schema = ds.schema_summary()
    else:
        # No uploads — don't even call the LLM; return helpful response directly
        return {
            "question": question,
            "route": "clarify",
            "route_reason": "No data uploaded yet.",
            "answer": "I don't have any data to analyze yet. Please upload a CSV or Excel file using the + button, then ask your question.",
        }, session.session_id

    initial: AgentState = {
        "question": question,
        "schema": schema,
        "session_context": context,
        "session_id": session.session_id,
        "previous_attempts": [],
    }
    final = graph.invoke(initial)
    return final, session.session_id


def record_query_after_run(session_id: str, final: AgentState) -> None:
    """Store a SUCCESSFUL SQL/viz attempt into the session as future context.

    Skipped silently for clarify/refuse routes and for failed runs — we
    don't want a broken query to poison subsequent follow-ups.
    """
    if final.get("route") not in ("sql", "viz"):
        return
    if final.get("error") or final.get("validation_failure"):
        return
    if not final.get("sql") or not final.get("rows"):
        return

    from app.session import QueryMemory

    memory = QueryMemory(
        question=final["question"],
        sql=final["sql"],
        columns=final.get("columns", []),
        row_count=final.get("row_count", 0),
        sample_rows=final.get("rows", [])[:3],
    )
    get_session_store().record_query(session_id, memory)


# Cache the compiled graph so we don't rebuild on every request.
_graph = None


def _get_graph():
    global _graph
    if _graph is None:
        _graph = build_graph()
    return _graph

