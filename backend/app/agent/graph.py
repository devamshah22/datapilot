"""LangGraph state machine for the agent.

Graph (after session 4):

                                                  ┌── retry (≤ N) ──┐
                                                  ▼                 │
    START → router ──────┬─→ write_sql → execute_sql → validator ─┐ │
                         │                                        │ │
                         │     ┌──────── invalid + retries left ──┘ │
                         │     │                                    │
                         │     └─────────────────────────────────────
                         │
                         ├─→ clarify_node ──────────────→ compose_answer → END
                         ├─→ refuse_node ───────────────→ compose_answer → END
                         │
                         └─→ (sql) ──valid──→ compose_answer → END
                             (viz) ──valid──→ make_chart → compose_answer → END

The validator is rule-based (no LLM call) so the retry decision is fast
and deterministic. Self-correction is bounded by settings.max_agent_retries.

Session 5 added in-session memory: ``run_agent`` accepts an optional
session id, loads recent queries as prompt context, and the caller
(API layer) records successful queries back into the SessionStore.
"""
from __future__ import annotations

from langgraph.graph import END, START, StateGraph

from app.agent.nodes import (
    chat_node,
    clarify_node,
    compose_answer_node,
    execute_python_node,
    execute_sql_node,
    make_chart_node,
    refuse_node,
    write_python_node,
    write_sql_node,
)
from app.agent.router import decide_after_router, router_node
from app.agent.state import AgentState
from app.agent.validator import validator_node
from app.config import settings
from app.session import get_session_store, render_session_context


def decide_after_validator(state: AgentState) -> str:
    """Where to go after the validator.

    - If validation revealed a problem (SQL error OR result invalid) AND
      we still have retries: send back to write_sql with the failed
      attempt(s) in state for context.
    - Otherwise proceed to chart-building (viz) or compose_answer.
    """
    has_problem = bool(state.get("error")) or bool(state.get("validation_failure"))
    retries_used = len(state.get("previous_attempts", []))

    if has_problem and retries_used < settings.max_agent_retries:
        return "retry"

    if state.get("route") == "viz" and not state.get("error"):
        return "make_chart"

    return "compose_answer"


def build_graph():
    builder = StateGraph(AgentState)
    builder.add_node("router", router_node)
    builder.add_node("write_sql", write_sql_node)
    builder.add_node("execute_sql", execute_sql_node)
    builder.add_node("validator", validator_node)
    builder.add_node("make_chart", make_chart_node)
    builder.add_node("write_python", write_python_node)
    builder.add_node("execute_python", execute_python_node)
    builder.add_node("chat_node", chat_node)
    builder.add_node("clarify_node", clarify_node)
    builder.add_node("refuse_node", refuse_node)
    builder.add_node("compose_answer", compose_answer_node)

    builder.add_edge(START, "router")

    # After router: branch on chosen route
    builder.add_conditional_edges(
        "router",
        decide_after_router,
        {
            "sql": "write_sql",
            "viz": "write_sql",
            "python": "write_python",
            "chat": "chat_node",
            "clarify": "clarify_node",
            "refuse": "refuse_node",
        },
    )

    # SQL path: write -> execute -> validate -> (retry | make_chart | compose)
    builder.add_edge("write_sql", "execute_sql")
    builder.add_edge("execute_sql", "validator")
    builder.add_conditional_edges(
        "validator",
        decide_after_validator,
        {
            "retry": "write_sql",
            "make_chart": "make_chart",
            "compose_answer": "compose_answer",
        },
    )
    builder.add_edge("make_chart", "compose_answer")

    # Python path: write -> execute -> compose
    builder.add_edge("write_python", "execute_python")
    builder.add_edge("execute_python", "compose_answer")

    # Chat / Clarify / refuse paths bypass tools entirely
    builder.add_edge("chat_node", "compose_answer")
    builder.add_edge("clarify_node", "compose_answer")
    builder.add_edge("refuse_node", "compose_answer")

    builder.add_edge("compose_answer", END)
    return builder.compile()


def run_agent(question: str, session_id: str | None = None, user_id: str | None = None) -> tuple[AgentState, str]:
    """Run the agent and return ``(final_state, session_id)``.

    Schema source priority:
      1. If the session has uploaded files → use per-session DatasetManager schema
      2. Otherwise → try restoring from Supabase Storage
      3. Otherwise → ask user to upload
    """
    graph = _get_graph()

    store = get_session_store()
    session = store.get_or_create(session_id, user_id=user_id)
    context = render_session_context(session)

    # Decide which schema + executor to use
    from app.tools.dataset_manager import get_dataset_manager
    mgr = get_dataset_manager()
    ds = mgr.get(session.session_id)

    if ds and ds.files:
        # User has uploaded data — use their schema
        schema = ds.schema_summary()
    else:
        # Check if files exist in Supabase Storage and restore them
        ds = _try_restore_from_storage(session.session_id, mgr)
        if ds and ds.files:
            schema = ds.schema_summary()
        else:
            # No uploads — don't even call the LLM; return helpful response directly
            return {
                "question": question,
                "route": "clarify",
                "route_reason": "No data uploaded yet.",
                "answer": "I don't have any data to analyze yet. Please upload a CSV or Excel file using the + button, then ask your question.",
            }, session.session_id

    initial: AgentState = {
        "question": question,
        "schema": schema,
        "session_context": context,
        "session_id": session.session_id,
        "previous_attempts": [],
    }
    final = graph.invoke(initial)
    return final, session.session_id


def record_query_after_run(session_id: str, final: AgentState) -> None:
    """Store a SUCCESSFUL SQL/viz attempt into the session as future context.

    Skipped silently for clarify/refuse routes and for failed runs — we
    don't want a broken query to poison subsequent follow-ups.
    """
    if final.get("route") not in ("sql", "viz"):
        return
    if final.get("error") or final.get("validation_failure"):
        return
    if not final.get("sql") or not final.get("rows"):
        return

    from app.session import QueryMemory

    memory = QueryMemory(
        question=final["question"],
        sql=final["sql"],
        columns=final.get("columns", []),
        row_count=final.get("row_count", 0),
        sample_rows=final.get("rows", [])[:3],
    )
    get_session_store().record_query(session_id, memory)


def _try_restore_from_storage(session_id: str, mgr) -> "SessionDataset | None":
    """Attempt to restore a session's uploaded files from Supabase Storage.

    Called when the DatasetManager doesn't have the session in memory
    (e.g., after a server restart or idle eviction). Downloads Parquet files
    from Supabase Storage and re-registers them with the DatasetManager.
    """
    from pathlib import Path
    from app.tools.dataset_manager import SessionDataset
    from app.tools.ingest import IngestedFile
    import duckdb

    try:
        from app.tools.file_storage import _get_storage, BUCKET_NAME

        storage = _get_storage()
        files = storage.from_(BUCKET_NAME).list(session_id)
        if not files:
            return None

        parquet_dir = Path(settings.dataset_path).parent.parent / "uploads" / session_id
        parquet_dir.mkdir(parents=True, exist_ok=True)

        ds = mgr.get_or_create(session_id, parquet_dir)

        for f in files:
            if not f["name"].endswith(".parquet"):
                continue
            table_name = f["name"].replace(".parquet", "")
            local_path = parquet_dir / f["name"]

            # Download if not already local
            if not local_path.exists():
                from app.tools.file_storage import download_parquet
                download_parquet(session_id, table_name, parquet_dir)

            # Get metadata from the parquet file
            con = duckdb.connect(":memory:")
            cols = con.execute(
                f"SELECT * FROM read_parquet('{local_path.as_posix()}') LIMIT 0"
            ).description
            row_count = con.execute(
                f"SELECT COUNT(*) FROM read_parquet('{local_path.as_posix()}')"
            ).fetchone()[0]
            con.close()

            ingested = IngestedFile(
                original_filename=f"{table_name}.parquet",
                table_name=table_name,
                parquet_path=local_path,
                columns=[c[0] for c in cols],
                dtypes={c[0]: str(c[1]) for c in cols},
                row_count=row_count,
                size_bytes=local_path.stat().st_size,
            )
            ds.add_file(ingested)

        return ds if ds.files else None
    except Exception as e:
        import logging
        logging.getLogger(__name__).warning("Failed to restore from storage: %s", e)
        return None


# Cache the compiled graph so we don't rebuild on every request.
_graph = None


def _get_graph():
    global _graph
    if _graph is None:
        _graph = build_graph()
    return _graph

