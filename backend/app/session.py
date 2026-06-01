"""In-session memory for follow-up questions.

A SessionStore keeps a rolling window of recent successful queries per
session, so questions like "now break that down by region" can be
resolved against the previous query rather than treated as fresh.

Design notes
------------
- We deliberately store SUMMARIES, not raw rows. Memory bloat and PII
  leakage are real concerns; the agent only needs to know what was
  asked, the SQL that ran, the resulting columns, and how many rows
  came back.
- TTL is enforced lazily on access (no background thread). For a single
  uvicorn worker on a portfolio deployment this is plenty; for multi-
  worker production, swap this for Redis (see docs/security.md).
- Process-local memory only. Restart wipes everything. That's a feature
  for the threat model and a non-issue for a session-scoped feature.
"""
from __future__ import annotations

import threading
import time
import uuid
from dataclasses import dataclass, field
from typing import Any


@dataclass
class QueryMemory:
    """A single successful query, retained for follow-up context."""
    question: str
    sql: str
    columns: list[str]
    row_count: int
    sample_rows: list[dict[str, Any]]  # ≤ 3 rows; intentionally tiny


@dataclass
class Session:
    """One user's conversational state."""
    session_id: str
    created_at: float
    last_accessed_at: float
    recent_queries: list[QueryMemory] = field(default_factory=list)


class SessionStore:
    """Thread-safe in-memory session store with lazy TTL cleanup."""

    def __init__(
        self,
        max_queries_per_session: int = 3,
        ttl_seconds: float = 30 * 60,  # 30 minutes idle
    ) -> None:
        self._sessions: dict[str, Session] = {}
        self._lock = threading.Lock()
        self.max_queries = max_queries_per_session
        self.ttl_seconds = ttl_seconds

    # --- session lifecycle ----------------------------------------------

    def get_or_create(self, session_id: str | None) -> Session:
        """Return an existing session or create a new one.

        If ``session_id`` is None, a fresh UUID is minted.
        """
        now = time.time()
        with self._lock:
            self._evict_expired_locked(now)

            if session_id and session_id in self._sessions:
                s = self._sessions[session_id]
                s.last_accessed_at = now
                return s

            new_id = session_id or uuid.uuid4().hex
            s = Session(
                session_id=new_id,
                created_at=now,
                last_accessed_at=now,
            )
            self._sessions[new_id] = s
            return s

    def record_query(
        self,
        session_id: str,
        memory: QueryMemory,
    ) -> None:
        """Append a query to the session, evicting oldest when over the cap."""
        with self._lock:
            s = self._sessions.get(session_id)
            if s is None:
                # Create-on-record so callers don't need to gate on existence
                s = Session(
                    session_id=session_id,
                    created_at=time.time(),
                    last_accessed_at=time.time(),
                )
                self._sessions[session_id] = s
            s.recent_queries.append(memory)
            if len(s.recent_queries) > self.max_queries:
                # Drop the oldest
                s.recent_queries = s.recent_queries[-self.max_queries:]
            s.last_accessed_at = time.time()

    # --- introspection (used by /sessions/{id} debug endpoint) ----------

    def get(self, session_id: str) -> Session | None:
        with self._lock:
            self._evict_expired_locked(time.time())
            return self._sessions.get(session_id)

    def __len__(self) -> int:
        with self._lock:
            return len(self._sessions)

    # --- internal -------------------------------------------------------

    def _evict_expired_locked(self, now: float) -> None:
        """Drop sessions that have been idle longer than TTL.

        Caller must hold the lock.
        """
        expired = [
            sid
            for sid, s in self._sessions.items()
            if now - s.last_accessed_at > self.ttl_seconds
        ]
        for sid in expired:
            del self._sessions[sid]


# Module-level singleton — created lazily.
_store: SessionStore | None = None


def get_session_store() -> SessionStore:
    global _store
    if _store is None:
        _store = SessionStore()
    return _store


def render_session_context(session: Session) -> str:
    """Render recent queries as a compact string for prompt injection.

    Returns an empty string when there's nothing to include — callers
    should branch on truthiness rather than concatenating empty context.
    """
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
