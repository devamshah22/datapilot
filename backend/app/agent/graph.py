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
"""
from __future__ import annotations

from langgraph.graph import END, START, StateGraph

from app.agent.nodes import (
    clarify_node,
    compose_answer_node,
    execute_sql_node,
    make_chart_node,
    refuse_node,
    write_sql_node,
)
from app.agent.router import decide_after_router, router_node
from app.agent.state import AgentState
from app.agent.validator import validator_node
from app.config import settings
from app.tools.sql import get_sql_tool


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

    # Clarify / refuse paths bypass SQL entirely
    builder.add_edge("clarify_node", "compose_answer")
    builder.add_edge("refuse_node", "compose_answer")

    builder.add_edge("compose_answer", END)
    return builder.compile()


def run_agent(question: str) -> AgentState:
    """Convenience entry-point: build the graph (cached), inject schema, run."""
    graph = _get_graph()
    schema = get_sql_tool().schema_summary()
    initial: AgentState = {
        "question": question,
        "schema": schema,
        "previous_attempts": [],
    }
    final = graph.invoke(initial)
    return final


# Cache the compiled graph so we don't rebuild on every request.
_graph = None


def _get_graph():
    global _graph
    if _graph is None:
        _graph = build_graph()
    return _graph
