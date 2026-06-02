"""In-session memory for follow-up questions.

Backends
--------
- ``InMemoryBackend``   process-local dict, dies on restart. Used by tests.
- ``SupabaseBackend``   Postgres via Supabase REST API. Persistent across
                        restarts, days, and multiple workers.

The choice is driven by ``settings.session_backend`` ("memory" or "supabase").
"""
from __future__ import annotations

import json
import logging
import threading
import time
import uuid
from dataclasses import asdict, dataclass, field
from typing import Any, Protocol

from app.config import settings

logger = logging.getLogger(__name__)


# --- Data models ------------------------------------------------------------


@dataclass
class QueryMemory:
    """A single successful query, retained for follow-up context."""
    question: str
    sql: str
    columns: list[str]
    row_count: int
    sample_rows: list[dict[str, Any]]


@dataclass
class Session:
    """One user's conversational state."""
    session_id: str
    created_at: float
    last_accessed_at: float
    recent_queries: list[QueryMemory] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> Session:
        return cls(
            session_id=data["session_id"],
            created_at=float(data["created_at"]),
            last_accessed_at=float(data["last_accessed_at"]),
            recent_queries=[
                QueryMemory(
                    question=q["question"],
                    sql=q["sql"],
                    columns=list(q["columns"]),
                    row_count=int(q["row_count"]),
                    sample_rows=list(q["sample_rows"]),
                )
                for q in data.get("recent_queries", [])
            ],
        )


# --- Backend protocol -------------------------------------------------------


class SessionBackend(Protocol):
    def get(self, session_id: str) -> Session | None: ...
    def save(self, session: Session) -> None: ...
    def delete(self, session_id: str) -> None: ...


# --- In-memory backend ------------------------------------------------------


class InMemoryBackend:
    """Process-local store with lazy TTL eviction. Thread-safe."""

    def __init__(self, ttl_seconds: float = 30 * 60) -> None:
        self._sessions: dict[str, Session] = {}
        self._expires_at: dict[str, float] = {}
        self._lock = threading.Lock()
        self._ttl = ttl_seconds

    def get(self, session_id: str) -> Session | None:
        with self._lock:
            self._evict_expired(time.time())
            return self._sessions.get(session_id)

    def save(self, session: Session) -> None:
        with self._lock:
            self._sessions[session.session_id] = session
            if self._ttl > 0:
                self._expires_at[session.session_id] = time.time() + self._ttl

    def delete(self, session_id: str) -> None:
        with self._lock:
            self._sessions.pop(session_id, None)
            self._expires_at.pop(session_id, None)

    def __len__(self) -> int:
        with self._lock:
            self._evict_expired(time.time())
            return len(self._sessions)

    def _evict_expired(self, now: float) -> None:
        if not self._ttl:
            return
        expired = [sid for sid, exp in self._expires_at.items() if exp <= now]
        for sid in expired:
            self._sessions.pop(sid, None)
            self._expires_at.pop(sid, None)


# --- Supabase backend -------------------------------------------------------


class SupabaseBackend:
    """Postgres via Supabase REST API. Sessions persist indefinitely."""

    def __init__(self, url: str, key: str) -> None:
        from supabase import create_client
        self._client = create_client(url, key)
        logger.info("SupabaseBackend connected to %s", url)

    def get(self, session_id: str) -> Session | None:
        # Fetch session row
        res = (
            self._client.table("sessions")
            .select("*")
            .eq("id", session_id)
            .execute()
        )
        if not res.data:
            return None
        row = res.data[0]

        # Fetch query memories ordered by position
        mem_res = (
            self._client.table("query_memories")
            .select("*")
            .eq("session_id", session_id)
            .order("position")
            .execute()
        )
        queries = [
            QueryMemory(
                question=m["question"],
                sql=m["sql"],
                columns=m["columns"] if isinstance(m["columns"], list) else json.loads(m["columns"]),
                row_count=m["row_count"],
                sample_rows=m["sample_rows"] if isinstance(m["sample_rows"], list) else json.loads(m["sample_rows"]),
            )
            for m in (mem_res.data or [])
        ]

        from datetime import datetime, timezone
        created = datetime.fromisoformat(row["created_at"]).timestamp()
        accessed = datetime.fromisoformat(row["last_accessed_at"]).timestamp()

        return Session(
            session_id=row["id"],
            created_at=created,
            last_accessed_at=accessed,
            recent_queries=queries,
        )

    def save(self, session: Session) -> None:
        from datetime import datetime, timezone

        now_iso = datetime.now(timezone.utc).isoformat()

        # Upsert session row
        self._client.table("sessions").upsert({
            "id": session.session_id,
            "title": session.recent_queries[0].question if session.recent_queries else None,
            "created_at": datetime.fromtimestamp(session.created_at, tz=timezone.utc).isoformat(),
            "last_accessed_at": now_iso,
        }).execute()

        # Replace query memories: delete old, insert new
        self._client.table("query_memories").delete().eq(
            "session_id", session.session_id
        ).execute()

        if session.recent_queries:
            rows = [
                {
                    "session_id": session.session_id,
                    "position": i,
                    "question": q.question,
                    "sql": q.sql,
                    "columns": q.columns,
                    "row_count": q.row_count,
                    "sample_rows": q.sample_rows,
                }
                for i, q in enumerate(session.recent_queries)
            ]
            self._client.table("query_memories").insert(rows).execute()

    def delete(self, session_id: str) -> None:
        # CASCADE handles query_memories and messages
        self._client.table("sessions").delete().eq("id", session_id).execute()

    # --- Message history (for frontend sidebar / chat UI) ---

    def save_message(
        self, session_id: str, role: str, content: str, metadata: dict[str, Any] | None = None
    ) -> None:
        self._client.table("messages").insert({
            "session_id": session_id,
            "role": role,
            "content": content,
            "metadata": metadata or {},
        }).execute()

    def get_messages(self, session_id: str) -> list[dict[str, Any]]:
        res = (
            self._client.table("messages")
            .select("role, content, metadata, created_at")
            .eq("session_id", session_id)
            .order("created_at")
            .execute()
        )
        return res.data or []

    def list_sessions(self) -> list[dict[str, Any]]:
        res = (
            self._client.table("sessions")
            .select("id, title, created_at, last_accessed_at")
            .order("last_accessed_at", desc=True)
            .limit(50)
            .execute()
        )
        return res.data or []


# --- Public store -----------------------------------------------------------


class SessionStore:
    """Stable public API on top of any SessionBackend."""

    def __init__(
        self,
        max_queries_per_session: int = 3,
        ttl_seconds: float | None = None,
        backend: SessionBackend | None = None,
    ) -> None:
        self.max_queries = max_queries_per_session
        self.ttl_seconds = float(
            ttl_seconds if ttl_seconds is not None else settings.session_ttl_seconds
        )
        self._backend: SessionBackend = (
            backend if backend is not None else _build_default_backend()
        )

    def get_or_create(self, session_id: str | None) -> Session:
        if session_id:
            existing = self._backend.get(session_id)
            if existing is not None:
                existing.last_accessed_at = time.time()
                self._backend.save(existing)
                return existing

        new_id = session_id or uuid.uuid4().hex
        now = time.time()
        s = Session(session_id=new_id, created_at=now, last_accessed_at=now)
        self._backend.save(s)
        return s

    def record_query(self, session_id: str, memory: QueryMemory) -> None:
        s = self._backend.get(session_id)
        if s is None:
            now = time.time()
            s = Session(session_id=session_id, created_at=now, last_accessed_at=now)
        s.recent_queries.append(memory)
        if len(s.recent_queries) > self.max_queries:
            s.recent_queries = s.recent_queries[-self.max_queries:]
        s.last_accessed_at = time.time()
        self._backend.save(s)

    def get(self, session_id: str) -> Session | None:
        return self._backend.get(session_id)

    def __len__(self) -> int:
        if isinstance(self._backend, InMemoryBackend):
            return len(self._backend)
        raise NotImplementedError("len() only supported on in-memory backend")


def _build_default_backend() -> SessionBackend:
    choice = (settings.session_backend or "memory").lower()
    if choice == "supabase":
        url = settings.supabase_url
        key = settings.supabase_key
        if not url or not key:
            logger.warning(
                "SESSION_BACKEND=supabase but SUPABASE_URL or SUPABASE_KEY "
                "is missing. Falling back to in-memory."
            )
            return InMemoryBackend()
        return SupabaseBackend(url=url, key=key)
    return InMemoryBackend()


# --- Module-level singleton + context renderer ------------------------------

_store: SessionStore | None = None


def get_session_store() -> SessionStore:
    global _store
    if _store is None:
        _store = SessionStore()
    return _store


def render_session_context(session: Session) -> str:
    if not session.recent_queries:
        return ""
    lines = ["PREVIOUS QUERIES IN THIS SESSION (most recent last):"]
    for i, q in enumerate(session.recent_queries, 1):
        lines.append(
            f"  {i}. Q: {q.question}\n"
            f"     SQL: {q.sql}\n"
            f"     Returned {q.row_count} rows with columns {q.columns}"
        )
    return "\n".join(lines)
