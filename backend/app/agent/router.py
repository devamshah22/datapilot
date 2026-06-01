"""Router node: classifies the question into one of four execution paths.

Uses Gemini with structured output (a Pydantic schema) so we always get a
valid route + reason without parsing free-form text.
"""
from __future__ import annotations

import logging
from typing import Any, Literal

from langchain_core.messages import HumanMessage, SystemMessage
from pydantic import BaseModel, Field

from app.agent.state import AgentState
from app.llm import get_structured_llm

logger = logging.getLogger(__name__)


class RouteDecision(BaseModel):
    """Structured output from the router LLM call."""

    route: Literal["sql", "viz", "clarify", "refuse"] = Field(
        ..., description="Which execution path to take."
    )
    reason: str = Field(
        ...,
        description=(
            "One short sentence justifying the route choice. "
            "For 'clarify', this is the question to ask the user. "
            "For 'refuse', this is the explanation."
        ),
    )


ROUTER_SYSTEM_PROMPT = """You are a routing classifier for a data analysis agent.

Given a user question and a dataset schema, decide which path to take:
- "sql"      — answerable with a single SQL query (aggregations, filters, ranking, group by)
- "viz"      — user explicitly asks for a chart/plot/graph/visualization, OR the answer is much
               clearer as a visualization (time series, distributions, comparisons across many groups)
- "clarify"  — question is genuinely under-specified (vague metric, no time frame, ambiguous "best")
               and a reasonable analyst would need to ask before answering
- "refuse"   — question requires forecasting (predict future values), causal claims (why did X happen),
               PII surfacing, or model-building the agent doesn't have. Explain rather than guess.

Rules:
- DEFAULT to "sql" when the question is clear and answerable with aggregation/filter SQL.
- Use "viz" when the user says "show me", "plot", "chart", "graph", "visualize", "trend over time".
- Use "clarify" sparingly. Don't ask trivial clarifications. Only when a single best
  interpretation is genuinely unclear (e.g., "how are sales doing?" — too vague).
- Use "refuse" for questions like "what will revenue be next quarter?" or "why did churn drop?"
  Causal and predictive questions cannot be answered honestly from data alone.

FOLLOW-UPS: If the prompt includes "PREVIOUS QUERIES IN THIS SESSION", the
current question may be a follow-up that refines an earlier one (e.g.,
"now break that down by region", "only the last 6 months", "make it a bar
chart instead"). In that case:
  - If the user asked to switch to a chart, route to "viz".
  - Otherwise route to the same kind of path the previous query used.
  - Do NOT clarify just because pronouns refer to prior context; resolving
    "that" against the most recent query is your job.

Return your decision via the structured schema. The `reason` field:
- For sql/viz: ONE short sentence explaining why this path fits.
- For clarify: the actual clarifying question to put in front of the user.
- For refuse: the actual explanation to put in front of the user.

LANGUAGE: Always write the `reason` field in English, even if the user's
question is in another language. Output is consumed by data teams who work
in English.
"""


_router_llm: Any = None


def _get_router_llm():
    global _router_llm
    if _router_llm is None:
        # with_structured_output guarantees parsed Pydantic output
        _router_llm = get_structured_llm(RouteDecision)
    return _router_llm


def router_node(state: AgentState) -> dict[str, Any]:
    question = state["question"]
    schema = state["schema"]
    session_context = state.get("session_context", "")

    llm = _get_router_llm()
    user_msg = f"Schema:\n{schema}\n\n"
    if session_context:
        user_msg += f"{session_context}\n\n"
    user_msg += f"Current question: {question}"

    decision: RouteDecision = llm.invoke([
        SystemMessage(content=ROUTER_SYSTEM_PROMPT),
        HumanMessage(content=user_msg),
    ])

    logger.info("Route: %s — %s", decision.route, decision.reason)
    return {"route": decision.route, "route_reason": decision.reason}


def decide_after_router(state: AgentState) -> str:
    """Conditional edge: where to go after the router based on the chosen route."""
    return state["route"]


def decide_after_sql(state: AgentState) -> str:
    """Conditional edge after execute_sql.

    Viz route with a successful SQL result needs a chart-spec step before
    the final answer composer. Otherwise jump straight to compose_answer.
    """
    if state.get("route") == "viz" and not state.get("error"):
        return "make_chart"
    return "compose_answer"
