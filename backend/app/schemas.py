"""Pydantic models for HTTP request/response bodies."""
from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel, Field


class AskRequest(BaseModel):
    question: str = Field(..., min_length=1, max_length=2000)


Route = Literal["sql", "viz", "clarify", "refuse"]


class AskResponse(BaseModel):
    question: str
    answer: str

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

    # Failure mode
    error: str | None = None
