"""In-session memory for follow-up questions.

A SessionStore keeps a rolling window of recent successful queries per
session, so questions like "now break that down by region" can be
resolved against the previous query rather than treated as fresh.

Backends
--------
- ``InMemoryBackend``  process-local dict, dies on restart. Good for
                       tests and local dev when you don't have Redis.
- ``RedisBackend``     Upstash Redis over its REST API. Survives
                       restarts; supports overnight resumption. TTL
                       is enforced server-side via SET ... EX.

The choice is driven by ``settings.session_backend`` ("memory" or "redis").
SessionStore exposes a stable public API; agent code never sees which
backend is in use.
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

    # --- (de)serialization ---------------------------------------------

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
    """Storage protocol for sessions. Both backends honour TTL."""

    def get(self, session_id: str) -> Session | None: ...
    def save(self, session: Session, ttl_seconds: int) -> None: ...
    def delete(self, session_id: str) -> None: ...


# --- In-memory backend ------------------------------------------------------


class InMemoryBackend:
    """Process-local store with lazy TTL eviction.

    Suitable for tests and local development; loses data on restart.
    Thread-safe.
    """

    def __init__(self) -> None:
        self._sessions: dict[str, Session] = {}
        self._expires_at: dict[str, float] = {}
        self._lock = threading.Lock()

    def get(self, session_id: str) -> Session | None:
        now = time.time()
        with self._lock:
            self._evict_expired_locked(now)
            return self._sessions.get(session_id)

    def save(self, session: Session, ttl_seconds: int) -> None:
        with self._lock:
            self._sessions[session.session_id] = session
            self._expires_at[session.session_id] = time.time() + ttl_seconds

    def delete(self, session_id: str) -> None:
        with self._lock:
            self._sessions.pop(session_id, None)
            self._expires_at.pop(session_id, None)

    def __len__(self) -> int:
        with self._lock:
            self._evict_expired_locked(time.time())
            return len(self._sessions)

    def _evict_expired_locked(self, now: float) -> None:
        expired = [sid for sid, exp in self._expires_at.items() if exp <= now]
        for sid in expired:
            self._sessions.pop(sid, None)
            self._expires_at.pop(sid, None)


# --- Redis backend (Upstash REST) -------------------------------------------


class RedisBackend:
    """Upstash Redis backend over the REST client.

    Sessions are serialized as JSON and stored under the key
    ``session:{session_id}``. TTL is enforced server-side via the ``ex``
    parameter on SET — every save renews the TTL ("sliding expiration").
    """

    KEY_PREFIX = "session:"

    def __init__(self, url: str, token: str) -> None:
        from upstash_redis import Redis

        self._redis = Redis(url=url, token=token)
        logger.info("RedisBackend initialised at %s", url)

    def _key(self, session_id: str) -> str:
        return f"{self.KEY_PREFIX}{session_id}"

    def get(self, session_id: str) -> Session | None:
        raw = self._redis.get(self._key(session_id))
        if raw is None:
            return None
        try:
            data = json.loads(raw)
        except (ValueError, TypeError) as e:
            logger.warning("Failed to deserialize session %s: %s", session_id, e)
            return None
        return Session.from_dict(data)

    def save(self, session: Session, ttl_seconds: int) -> None:
        payload = json.dumps(session.to_dict(), default=str)
        self._redis.set(self._key(session.session_id), payload, ex=ttl_seconds)

    def delete(self, session_id: str) -> None:
        self._redis.delete(self._key(session_id))


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
        # Float on the public API for compat with previous tests; int internally.
        self.ttl_seconds = float(
            ttl_seconds if ttl_seconds is not None else settings.session_ttl_seconds
        )
        # Important: use ``is None`` rather than ``backend or default``.
        # ``InMemoryBackend`` defines ``__len__``, so an empty backend is falsy
        # and ``or`` would skip past it to the default Redis backend.
        self._backend: SessionBackend = (
            backend if backend is not None else _build_default_backend()
        )

    # --- session lifecycle ----------------------------------------------

    def get_or_create(self, session_id: str | None) -> Session:
        if session_id:
            existing = self._backend.get(session_id)
            if existing is not None:
                # Touching a session refreshes TTL.
                existing.last_accessed_at = time.time()
                self._backend.save(existing, int(self.ttl_seconds))
                return existing

        new_id = session_id or uuid.uuid4().hex
        now = time.time()
        s = Session(session_id=new_id, created_at=now, last_accessed_at=now)
        self._backend.save(s, int(self.ttl_seconds))
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
        self._backend.save(s, int(self.ttl_seconds))

    def get(self, session_id: str) -> Session | None:
        return self._backend.get(session_id)

    def __len__(self) -> int:
        # Only meaningful for in-memory backends; Redis would need SCAN.
        if isinstance(self._backend, InMemoryBackend):
            return len(self._backend)
        raise NotImplementedError(
            "len() is only supported on the in-memory backend; "
            "use Redis SCAN if you need session counts in production."
        )


def _build_default_backend() -> SessionBackend:
    """Pick a backend based on settings; fall back to in-memory on misconfiguration."""
    choice = (settings.session_backend or "memory").lower()
    if choice == "redis":
        url = settings.upstash_redis_rest_url
        token = settings.upstash_redis_rest_token
        if not url or not token:
            logger.warning(
                "SESSION_BACKEND=redis but UPSTASH_REDIS_REST_URL or "
                "UPSTASH_REDIS_REST_TOKEN is missing. Falling back to in-memory."
            )
            return InMemoryBackend()
        return RedisBackend(url=url, token=token)
    return InMemoryBackend()


# --- Module-level singleton + context renderer (unchanged) ------------------

_store: SessionStore | None = None


def get_session_store() -> SessionStore:
    global _store
    if _store is None:
        _store = SessionStore()
    return _store


def render_session_context(session: Session) -> str:
    """Render recent queries as a compact string for prompt injection."""
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
