"""FastAPI app for DataPilot.

v1 endpoints:
    GET  /health         — liveness probe
    GET  /schema         — current dataset schema (debug aid)
    POST /ask            — ask one question, get one answer

Safety:
    - Per-IP rate limiting via slowapi
    - CORS allow-list (configurable via env)
    - SQL execution has a hard timeout (see tools/sql.py)
    - Mutating SQL is rejected at the tool layer
"""
from __future__ import annotations

import logging

from fastapi import FastAPI, HTTPException, Request
from fastapi.middleware.cors import CORSMiddleware
from slowapi import Limiter, _rate_limit_exceeded_handler
from slowapi.errors import RateLimitExceeded
from slowapi.util import get_remote_address

from app.agent.graph import run_agent
from app.config import settings
from app.schemas import AskRequest, AskResponse
from app.tools.sql import get_sql_tool

logging.basicConfig(
    level=settings.log_level,
    format="%(asctime)s %(levelname)s %(name)s: %(message)s",
)
logger = logging.getLogger("datapilot")

# --- FastAPI app + middleware ----------------------------------------------

app = FastAPI(
    title="DataPilot",
    version="0.4.0",
    description="Conversational data analysis for CSV files.",
)

# Rate limiter: per remote IP. Default limit applies to all routes that
# don't specify their own; /ask gets a stricter limit (LLM-bound).
limiter = Limiter(key_func=get_remote_address, default_limits=[settings.rate_limit_default])
app.state.limiter = limiter
app.add_exception_handler(RateLimitExceeded, _rate_limit_exceeded_handler)

# CORS: allow-list driven by env. Default "*" for dev; tighten in deploy.
app.add_middleware(
    CORSMiddleware,
    allow_origins=settings.cors_origins_list,
    allow_credentials=False,  # we don't use cookies; safer with allow_origins=*
    allow_methods=["GET", "POST", "OPTIONS"],
    allow_headers=["*"],
)


# --- Lifecycle --------------------------------------------------------------


@app.on_event("startup")
def _warm_up() -> None:
    # Eagerly load the dataset so the first request isn't slow.
    tool = get_sql_tool()
    logger.info("Dataset loaded from %s", tool.csv_path)
    logger.info("Table: %s", tool.table_name)
    logger.info(
        "Rate limits: default=%s, /ask=%s",
        settings.rate_limit_default,
        settings.rate_limit_ask,
    )


# --- Endpoints --------------------------------------------------------------


@app.get("/health")
@limiter.limit(settings.rate_limit_default)
def health(request: Request) -> dict[str, str]:
    return {"status": "ok"}


@app.get("/schema")
@limiter.limit(settings.rate_limit_default)
def schema(request: Request) -> dict[str, str]:
    return {"schema": get_sql_tool().schema_summary()}


@app.post("/ask", response_model=AskResponse)
@limiter.limit(settings.rate_limit_ask)
def ask(request: Request, req: AskRequest) -> AskResponse:
    try:
        final = run_agent(req.question)
    except Exception as e:  # noqa: BLE001
        logger.exception("Agent crashed")
        raise HTTPException(status_code=500, detail=f"Agent error: {e}") from e

    return AskResponse(
        question=req.question,
        answer=final.get("answer", ""),
        route=final.get("route"),
        route_reason=final.get("route_reason"),
        sql=final.get("sql"),
        columns=final.get("columns", []),
        rows=final.get("rows", []),
        row_count=final.get("row_count", 0),
        chart_spec=final.get("chart_spec") or None,
        chart_error=final.get("chart_error") or None,
        retry_count=len(final.get("previous_attempts", [])),
        previous_attempts=final.get("previous_attempts", []),
        validation_failure=final.get("validation_failure") or None,
        error=final.get("error") or None,
    )
