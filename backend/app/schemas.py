"""Pydantic models for HTTP request/response bodies."""
from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel, Field


class AskRequest(BaseModel):
    question: str = Field(..., min_length=1, max_length=2000)
    # Optional. If absent, the server mints a new session and returns it
    # in the response so the client can send it back on the next turn.
    session_id: str | None = Field(default=None, max_length=64)


Route = Literal["sql", "viz", "clarify", "refuse"]


class AskResponse(BaseModel):
    question: str
    answer: str
    session_id: str  # always returned, even if the client didn't supply one

    # Routing transparency (debug aid + recruiter-friendly traces)
    route: Route | None = None
    route_reason: str | None = None

    # SQL path
    sql: str | None = None
    columns: list[str] = Field(default_factory=list)
    rows: list[dict[str, Any]] = Field(default_factory=list)
    row_count: int = 0

    # Viz path
    chart_spec: dict[str, Any] | None = None
    chart_error: str | None = None

    # Self-correction transparency (dev / debug — not surfaced to end user text)
    retry_count: int = 0
    previous_attempts: list[dict[str, Any]] = Field(default_factory=list)
    validation_failure: str | None = None

    # Failure mode
    error: str | None = None


class SessionInfo(BaseModel):
    """Returned by GET /sessions/{id} for debugging / observability."""
    session_id: str
    created_at: float
    last_accessed_at: float
    recent_queries: list[dict[str, Any]] = Field(default_factory=list)
