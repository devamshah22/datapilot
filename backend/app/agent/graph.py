"""LangGraph state machine for the v1 agent.

v1 graph:
    write_sql -> execute_sql -> compose_answer -> END

No conditional edges, no loops, no retries. The simplest agent that can
end-to-end answer a question with SQL.

Future versions will add:
    - router node (SQL vs Python vs Viz vs Clarify) BEFORE write_sql
    - validator node AFTER execute_sql
    - conditional edge from validator back to write_sql for self-correction
"""
from __future__ import annotations

from langgraph.graph import END, START, StateGraph

from app.agent.nodes import compose_answer_node, execute_sql_node, write_sql_node
from app.agent.state import AgentState
from app.tools.sql import get_sql_tool


def build_graph():
    builder = StateGraph(AgentState)
    builder.add_node("write_sql", write_sql_node)
    builder.add_node("execute_sql", execute_sql_node)
    builder.add_node("compose_answer", compose_answer_node)

    builder.add_edge(START, "write_sql")
    builder.add_edge("write_sql", "execute_sql")
    builder.add_edge("execute_sql", "compose_answer")
    builder.add_edge("compose_answer", END)

    return builder.compile()


def run_agent(question: str) -> AgentState:
    """Convenience entry-point: build the graph (cached), inject schema, run."""
    graph = _get_graph()
    schema = get_sql_tool().schema_summary()
    initial: AgentState = {"question": question, "schema": schema}
    final = graph.invoke(initial)
    return final  # LangGraph returns the merged final state dict


# Cache the compiled graph so we don't rebuild on every request
_graph = None


def _get_graph():
    global _graph
    if _graph is None:
        _graph = build_graph()
    return _graph
