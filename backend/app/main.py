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
from datetime import datetime, timezone

from fastapi import Body, FastAPI, File, HTTPException, Request, UploadFile
from fastapi.middleware.cors import CORSMiddleware
from slowapi import Limiter, _rate_limit_exceeded_handler
from slowapi.errors import RateLimitExceeded
from slowapi.util import get_remote_address

from app.agent.graph import record_query_after_run, run_agent
from app.config import settings
from app.schemas import AskRequest, AskResponse, MessageOut, SessionDetail, SessionListItem
from app.session import SupabaseBackend, get_session_store
from app.tools.dataset_manager import (
    MemoryBudgetExceeded,
    get_dataset_manager,
)
from app.tools.ingest import IngestionError, IngestedFile, MAX_FILES_PER_BATCH, validate_and_convert
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

    # Record into agent memory (for follow-up prompt context)
    record_query_after_run(session_id, final)

    # Persist full messages into Supabase (for frontend chat history)
    store = get_session_store()
    backend = store._backend
    if isinstance(backend, SupabaseBackend):
        # Save user message
        backend.save_message(session_id, "user", req.question)
        # Save assistant message with metadata
        metadata = {
            "route": final.get("route"),
            "route_reason": final.get("route_reason"),
            "sql": final.get("sql"),
            "columns": final.get("columns", []),
            "row_count": final.get("row_count", 0),
            "chart_spec": final.get("chart_spec"),
            "error": final.get("error"),
        }
        backend.save_message(session_id, "assistant", final.get("answer", ""), metadata)

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


@app.post("/sessions/{sid}/upload")
@limiter.limit(settings.rate_limit_ask)
def upload_files(
    request: Request,
    sid: str,
    files: list[UploadFile] = File(...),
) -> dict:
    """Upload CSV/Excel files to a session. Converts to Parquet on ingest.

    - Max 5 files per batch
    - Max 10 MB per file
    - Files are losslessly compressed to Parquet
    - Each file becomes a queryable table in the session's DuckDB
    """
    if len(files) > MAX_FILES_PER_BATCH:
        raise HTTPException(
            status_code=400,
            detail=f"Max {MAX_FILES_PER_BATCH} files per upload. Got {len(files)}.",
        )

    # Ensure the session exists in the session store
    store = get_session_store()
    store.get_or_create(sid)

    # Set up parquet output directory
    from pathlib import Path
    parquet_dir = Path(settings.dataset_path).parent.parent / "uploads" / sid
    mgr = get_dataset_manager()
    mgr.get_or_create(sid, parquet_dir)

    results = []
    errors = []

    for upload in files:
        try:
            ds = mgr.get(sid)
            ingested = validate_and_convert(
                filename=upload.filename or "unknown.csv",
                file_data=upload.file,
                output_dir=parquet_dir,
                existing_table_names=ds.table_names if ds else [],
            )
            mgr.add_file(sid, ingested)
            results.append({
                "filename": ingested.original_filename,
                "table_name": ingested.table_name,
                "rows": ingested.row_count,
                "columns": ingested.columns,
                "parquet_size_kb": round(ingested.size_bytes / 1024, 1),
            })
        except IngestionError as e:
            errors.append({"filename": upload.filename, "error": str(e)})
        except MemoryBudgetExceeded as e:
            errors.append({"filename": upload.filename, "error": str(e)})
            break  # No point trying more files if budget is hit

    status = "ok" if not errors else ("partial" if results else "failed")
    return {
        "status": status,
        "session_id": sid,
        "uploaded": results,
        "errors": errors,
    }


@app.get("/sessions/{sid}/tables")
@limiter.limit(settings.rate_limit_default)
def list_tables(request: Request, sid: str) -> dict:
    """List tables available in a session's dataset."""
    mgr = get_dataset_manager()
    ds = mgr.get(sid)
    if ds is None or not ds.files:
        return {"session_id": sid, "tables": []}
    return {
        "session_id": sid,
        "tables": [
            {
                "table_name": f.table_name,
                "filename": f.original_filename,
                "rows": f.row_count,
                "columns": f.columns,
            }
            for f in ds.files
        ],
    }


@app.get("/sessions", response_model=list[SessionListItem])
@limiter.limit(settings.rate_limit_default)
def list_sessions(request: Request) -> list[SessionListItem]:
    """List recent sessions for the sidebar."""
    store = get_session_store()
    backend = store._backend
    if not isinstance(backend, SupabaseBackend):
        return []
    rows = backend.list_sessions()
    return [
        SessionListItem(
            session_id=r["id"],
            title=r.get("title"),
            created_at=r["created_at"],
            last_accessed_at=r["last_accessed_at"],
        )
        for r in rows
    ]


@app.get("/sessions/{sid}", response_model=SessionDetail)
@limiter.limit(settings.rate_limit_default)
def get_session(request: Request, sid: str) -> SessionDetail:
    """Get full message history for a session."""
    store = get_session_store()
    backend = store._backend
    if not isinstance(backend, SupabaseBackend):
        raise HTTPException(status_code=404, detail="session storage not available")
    s = backend.get(sid)
    if s is None:
        raise HTTPException(status_code=404, detail="session not found")
    messages_raw = backend.get_messages(sid)
    messages = [
        MessageOut(
            role=m["role"],
            content=m["content"],
            metadata=m.get("metadata", {}),
            created_at=m.get("created_at"),
        )
        for m in messages_raw
    ]
    from datetime import datetime, timezone
    return SessionDetail(
        session_id=s.session_id,
        title=s.recent_queries[0].question if s.recent_queries else None,
        created_at=datetime.fromtimestamp(s.created_at, tz=timezone.utc).isoformat(),
        last_accessed_at=datetime.fromtimestamp(s.last_accessed_at, tz=timezone.utc).isoformat(),
        messages=messages,
    )


@app.delete("/sessions/{sid}")
@limiter.limit(settings.rate_limit_default)
def delete_session(request: Request, sid: str) -> dict[str, str]:
    """Delete a session and all its messages."""
    store = get_session_store()
    backend = store._backend
    if not isinstance(backend, SupabaseBackend):
        raise HTTPException(status_code=404, detail="session storage not available")
    backend.delete(sid)
    return {"status": "deleted", "session_id": sid}
