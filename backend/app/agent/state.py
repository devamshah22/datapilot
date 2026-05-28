"""LangGraph state shared across nodes.

We use a TypedDict so LangGraph can merge updates correctly. Keep it small
in v1 — every field added here pushes us toward over-engineering before
we know what we need.
"""
from __future__ import annotations

from typing import Any, TypedDict


class AgentState(TypedDict, total=False):
    # Input
    question: str

    # Schema injected at graph entry so nodes don't reach into the SQL tool
    schema: str

    # Filled by write_sql node
    sql: str

    # Filled by execute_sql node
    columns: list[str]
    rows: list[dict[str, Any]]
    row_count: int
    error: str

    # Final user-facing answer composed at the end of the graph
    answer: str
