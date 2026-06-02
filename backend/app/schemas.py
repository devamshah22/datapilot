"""Pydantic models for HTTP request/response bodies."""
from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel, Field


class AskRequest(BaseModel):
    question: str = Field(..., min_length=1, max_length=2000)
    session_id: str | None = Field(default=None, max_length=64)


Route = Literal["sql", "viz", "clarify", "refuse"]


class MessageOut(BaseModel):
    """A single message in the conversation history."""
    role: str  # "user" or "assistant"
    content: str
    metadata: dict[str, Any] = Field(default_factory=dict)
    created_at: str | None = None


class SessionListItem(BaseModel):
    """Returned in GET /sessions list."""
    session_id: str
    title: str | None = None
    created_at: str
    last_accessed_at: str


class AskResponse(BaseModel):
    question: str
    answer: str
    session_id: str

    # Routing transparency
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

    # Self-correction transparency
    retry_count: int = 0
    previous_attempts: list[dict[str, Any]] = Field(default_factory=list)
    validation_failure: str | None = None

    # Failure mode
    error: str | None = None


class SessionDetail(BaseModel):
    """Returned by GET /sessions/{id} — full message history."""
    session_id: str
    title: str | None = None
    created_at: str
    last_accessed_at: str
    messages: list[MessageOut] = Field(default_factory=list)
