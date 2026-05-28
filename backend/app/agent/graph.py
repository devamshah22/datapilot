"""LangGraph state machine for the agent.

Graph (after session 3):

                                 ┌─ clarify ─┐
                                 │           │
    START → router ───────────── ┼─ refuse ──┼──→ compose_answer → END
                                 │           │
                                 └─ write_sql → execute_sql ─┐
                                                  │          │
                                                  ↓          │
                                              make_chart ────┘
                                              (only if route=viz)

The router decides path; compose_answer normalizes output across all
paths so the API response shape stays stable.
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
from app.agent.router import decide_after_router, decide_after_sql, router_node
from app.agent.state import AgentState
from app.tools.sql import get_sql_tool


def build_graph():
    builder = StateGraph(AgentState)
    builder.add_node("router", router_node)
    builder.add_node("write_sql", write_sql_node)
    builder.add_node("execute_sql", execute_sql_node)
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

    # SQL path: write -> execute -> (maybe make_chart) -> compose
    builder.add_edge("write_sql", "execute_sql")
    builder.add_conditional_edges(
        "execute_sql",
        decide_after_sql,
        {
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
    initial: AgentState = {"question": question, "schema": schema}
    final = graph.invoke(initial)
    return final


# Cache the compiled graph so we don't rebuild on every request.
_graph = None


def _get_graph():
    global _graph
    if _graph is None:
        _graph = build_graph()
    return _graph
