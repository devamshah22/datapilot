"""FastAPI app for DataPilot.

v1 endpoints:
    GET  /health             — liveness probe
    GET  /schema             — current dataset schema (debug aid)
    POST /ask                — ask one question, get one answer
    GET  /sessions/{sid}     — debug: inspect what the agent remembers

Safety:
    - Per-IP rate limiting via slowapi
    - CORS allow-list (configurable via env)
    - SQL execution has a hard timeout (see tools/sql.py)
    - Mutating SQL is rejected at the tool layer
"""
import logging
from dataclasses import asdict

from fastapi import Body, FastAPI, HTTPException, Request
from fastapi.middleware.cors import CORSMiddleware
from slowapi import Limiter, _rate_limit_exceeded_handler
from slowapi.errors import RateLimitExceeded
from slowapi.util import get_remote_address

from app.agent.graph import record_query_after_run, run_agent
from app.config import settings
from app.schemas import AskRequest, AskResponse, SessionInfo
from app.session import get_session_store
from app.tools.sql import get_sql_tool

logging.basicConfig(
    level=settings.log_level,
    format="%(asctime)s %(levelname)s %(name)s: %(message)s",
)
logger = logging.getLogger("datapilot")

# --- FastAPI app + middleware ----------------------------------------------

app = FastAPI(
    title="DataPilot",
    version="0.5.0",
    description="Conversational data analysis for CSV files.",
)

limiter = Limiter(key_func=get_remote_address, default_limits=[settings.rate_limit_default])
app.state.limiter = limiter
app.add_exception_handler(RateLimitExceeded, _rate_limit_exceeded_handler)

app.add_middleware(
    CORSMiddleware,
    allow_origins=settings.cors_origins_list,
    allow_credentials=False,
    allow_methods=["GET", "POST", "OPTIONS"],
    allow_headers=["*"],
)


# --- Lifecycle --------------------------------------------------------------


@app.on_event("startup")
def _warm_up() -> None:
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
def ask(request: Request, req: AskRequest = Body(...)) -> AskResponse:
    try:
        final, session_id = run_agent(req.question, session_id=req.session_id)
    except Exception as e:  # noqa: BLE001
        logger.exception("Agent crashed")
        raise HTTPException(status_code=500, detail=f"Agent error: {e}") from e

    # Record this turn into the session AFTER we have a final answer, so
    # follow-up questions in the next turn can use it as context.
    record_query_after_run(session_id, final)

    return AskResponse(
        question=req.question,
        answer=final.get("answer", ""),
        session_id=session_id,
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


@app.get("/sessions/{sid}", response_model=SessionInfo)
@limiter.limit(settings.rate_limit_default)
def get_session(request: Request, sid: str) -> SessionInfo:
    """Debug endpoint — what does the agent remember for this session?"""
    s = get_session_store().get(sid)
    if s is None:
        raise HTTPException(status_code=404, detail="session not found or expired")
    return SessionInfo(
        session_id=s.session_id,
        created_at=s.created_at,
        last_accessed_at=s.last_accessed_at,
        recent_queries=[asdict(q) for q in s.recent_queries],
    )
