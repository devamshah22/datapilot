"""LangGraph state shared across nodes.

We use a TypedDict so LangGraph can merge updates correctly. Keep it small —
every field added here pushes us toward over-engineering before we know
what we need.
"""
from __future__ import annotations

from typing import Any, Literal, TypedDict

Route = Literal["sql", "viz", "clarify", "refuse"]


class AttemptRecord(TypedDict):
    sql: str
    error: str  # empty string when the attempt succeeded


class AgentState(TypedDict, total=False):
    # Input
    question: str

    # Schema injected at graph entry so nodes don't reach into the SQL tool
    schema: str

    # Filled by router_node
    route: Route
    route_reason: str

    # Filled / overwritten by write_sql each attempt
    sql: str

    # Filled / overwritten by execute_sql each attempt
    columns: list[str]
    rows: list[dict[str, Any]]
    row_count: int
    error: str  # SQL execution failed on the latest attempt

    # Self-correction tracking (week 4)
    retry_count: int
    previous_attempts: list[AttemptRecord]
    validation_failure: str  # set by validator when result is suspicious

    # Filled by make_chart node (only on viz route)
    chart_spec: dict[str, Any]
    chart_error: str  # chart build failed (SQL still succeeded)

    # Final user-facing answer composed at the end of the graph
    answer: str
