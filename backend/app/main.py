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
from app.auth import AuthMiddleware
from app.config import settings
from app.schemas import AskRequest, AskResponse, MessageOut, SessionDetail, SessionListItem
from app.session import SupabaseBackend, get_session_store
from app.tools.dataset_manager import (
    MemoryBudgetExceeded,
    get_dataset_manager,
)
from app.tools.ingest import IngestionError, IngestedFile, MAX_FILES_PER_BATCH, validate_and_convert


def _verify_session_ownership(sid: str, user_id: str, allow_creation: bool = False) -> None:
    """Verify the authenticated user owns this session. Raises 403 if not.

    If allow_creation=True and the session doesn't exist, returns silently
    (the caller will create it with the correct user_id).
    """
    store = get_session_store()
    backend = store._backend
    if not isinstance(backend, SupabaseBackend):
        return  # In-memory backend has no user scoping
    res = backend._client.table("sessions").select("user_id").eq("id", sid).execute()
    if not res.data:
        if allow_creation:
            return  # Session will be created by the caller
        raise HTTPException(status_code=404, detail="Session not found")
    owner = res.data[0].get("user_id")
    if owner and owner != user_id:
        raise HTTPException(status_code=403, detail="Access denied")

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

# Auth middleware — validates JWT, attaches user_id to request.state
app.add_middleware(AuthMiddleware)

# CORS middleware — added LAST so it's the outermost in Starlette's stack.
# This ensures CORS headers are present on ALL responses, including 401s from auth.
app.add_middleware(
    CORSMiddleware,
    allow_origins=settings.cors_origins_list,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


# --- Lifecycle --------------------------------------------------------------


@app.on_event("startup")
def _warm_up() -> None:
    logger.info(
        "Rate limits: default=%s, /ask=%s",
        settings.rate_limit_default,
        settings.rate_limit_ask,
    )
    # Ensure Supabase Storage bucket exists
    try:
        from app.tools.file_storage import ensure_bucket_exists
        ensure_bucket_exists()
    except Exception as e:
        logger.warning("Could not ensure storage bucket: %s", e)


# --- Endpoints --------------------------------------------------------------


@app.get("/health")
@limiter.limit(settings.rate_limit_default)
def health(request: Request) -> dict[str, str]:
    return {"status": "ok"}


@app.get("/schema")
@limiter.limit(settings.rate_limit_default)
def schema(request: Request) -> dict[str, str]:
    """Show the schema for the user's most recent session with uploads."""
    user_id = request.state.user_id
    store = get_session_store()
    backend = store._backend
    if isinstance(backend, SupabaseBackend):
        sessions = backend.list_sessions(user_id=user_id)
        if sessions:
            mgr = get_dataset_manager()
            for s in sessions:
                ds = mgr.get(s["id"])
                if ds and ds.files:
                    return {"schema": ds.schema_summary()}
    return {"schema": "No data uploaded yet. Upload a CSV or Excel file to see the schema."}


@app.post("/ask", response_model=AskResponse)
@limiter.limit(settings.rate_limit_ask)
def ask(request: Request, req: AskRequest = Body(...)) -> AskResponse:
    user_id = request.state.user_id
    # If a session_id is provided, verify the user owns it
    if req.session_id:
        _verify_session_ownership(req.session_id, user_id)
    try:
        final, session_id = run_agent(req.question, session_id=req.session_id, user_id=user_id)
    except Exception as e:  # noqa: BLE001
        logger.exception("Agent crashed")
        raise HTTPException(
            status_code=500,
            detail="Something went wrong processing your request. Please try again.",
        ) from e

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
    user_id = request.state.user_id
    _verify_session_ownership(sid, user_id, allow_creation=True)
    store = get_session_store()
    store.get_or_create(sid, user_id=user_id)

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
            # Persist to Supabase Storage for cross-restart survival
            try:
                from app.tools.file_storage import upload_parquet
                upload_parquet(sid, ingested.table_name, ingested.parquet_path)
            except Exception as e:
                logger.warning("Failed to persist %s to storage: %s", ingested.table_name, e)
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
    user_id = request.state.user_id
    _verify_session_ownership(sid, user_id)
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
    """List recent sessions for the sidebar — filtered by authenticated user."""
    user_id = request.state.user_id
    store = get_session_store()
    backend = store._backend
    if not isinstance(backend, SupabaseBackend):
        return []
    rows = backend.list_sessions(user_id=user_id)
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
    user_id = request.state.user_id
    _verify_session_ownership(sid, user_id)
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
    """Delete a session and all its messages, files, and in-memory data."""
    user_id = request.state.user_id
    _verify_session_ownership(sid, user_id)

    store = get_session_store()
    backend = store._backend
    if not isinstance(backend, SupabaseBackend):
        raise HTTPException(status_code=404, detail="session storage not available")

    # 1. Delete uploaded files from Supabase Storage FIRST (before DB records)
    storage_error = None
    try:
        from app.tools.file_storage import delete_session_files
        delete_session_files(sid)
    except Exception as e:
        storage_error = str(e)
        logger.error("Failed to delete storage files for %s: %s", sid, e)

    # 2. Close in-memory DuckDB connection
    mgr = get_dataset_manager()
    mgr.close_session(sid)

    # 3. Delete from database (sessions + messages + query_memories via CASCADE)
    backend.delete(sid)

    if storage_error:
        return {"status": "partial", "session_id": sid, "warning": f"Session deleted but file cleanup failed: {storage_error}"}

    return {"status": "deleted", "session_id": sid}
