"""Per-session dataset manager.

Each session that has uploaded files gets its own DuckDB connection that
queries Parquet files on disk via ``read_parquet()``. This keeps RAM low
(DuckDB streams from disk) while supporting multi-file, multi-table
querying per session.

Key design decisions:
  - DuckDB connections are created lazily on first query after upload.
  - Tables are VIEWs over ``read_parquet('path')`` — no data copied into RAM.
  - Idle sessions (no query in 15 min) have their connection closed to free
    file handles. Re-opened transparently on next access.
  - A global memory budget caps the total estimated data footprint across
    all active sessions. If exceeded, oldest idle sessions are evicted
    first; if still over, new uploads get HTTP 503.
"""
from __future__ import annotations

import logging
import threading
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import duckdb

from app.config import settings
from app.tools.ingest import IngestedFile

logger = logging.getLogger(__name__)

# --- Configuration -----------------------------------------------------------

# Global budget for estimated data across all sessions (bytes).
# Conservative for a 512MB deploy: leave ~250MB for app + libraries.
GLOBAL_BUDGET_BYTES = 250 * 1024 * 1024  # 250 MB

# Idle timeout: close DuckDB connections after this many seconds of inactivity.
IDLE_TIMEOUT_SECONDS = 15 * 60  # 15 minutes

# Expansion factor: Parquet on disk → estimated RAM when DuckDB scans it.
# DuckDB streams lazily so this is an overestimate, which is safe.
EXPANSION_FACTOR = 3.0


# --- Data classes ------------------------------------------------------------


@dataclass
class SessionDataset:
    """Tracks uploaded files and the DuckDB connection for one session."""
    session_id: str
    files: list[IngestedFile] = field(default_factory=list)
    parquet_dir: Path | None = None
    # DuckDB connection — created lazily, closed on idle eviction
    _con: duckdb.DuckDBPyConnection | None = field(default=None, repr=False)
    _last_query_at: float = field(default_factory=time.time)
    _schema_cache: str | None = field(default=None, repr=False)

    @property
    def total_parquet_bytes(self) -> int:
        return sum(f.size_bytes for f in self.files)

    @property
    def estimated_memory(self) -> float:
        return self.total_parquet_bytes * EXPANSION_FACTOR

    @property
    def is_idle(self) -> bool:
        return (time.time() - self._last_query_at) > IDLE_TIMEOUT_SECONDS

    @property
    def table_names(self) -> list[str]:
        return [f.table_name for f in self.files]

    def add_file(self, ingested: IngestedFile) -> None:
        self.files.append(ingested)
        self._schema_cache = None  # invalidate
        # If connection is already open, register the new table immediately
        if self._con is not None:
            self._register_table(ingested)

    def get_connection(self) -> duckdb.DuckDBPyConnection:
        """Get or create the DuckDB connection with all tables registered."""
        self._last_query_at = time.time()
        if self._con is None:
            self._con = duckdb.connect(":memory:")
            for f in self.files:
                self._register_table(f)
        return self._con

    def close(self) -> None:
        """Close the DuckDB connection to free resources."""
        if self._con is not None:
            try:
                self._con.close()
            except Exception:
                pass
            self._con = None
        self._schema_cache = None

    def schema_summary(self, sample_values: int = 2) -> str:
        """Multi-table schema summary for the LLM prompt."""
        if self._schema_cache is not None:
            return self._schema_cache

        if not self.files:
            return "(no data uploaded yet)"

        con = self.get_connection()
        lines = []
        for f in self.files:
            lines.append(f"Table: {f.table_name} ({f.row_count:,} rows)")
            lines.append("Columns:")
            cols = con.execute(f"DESCRIBE {f.table_name}").fetchall()
            for name, dtype, *_ in cols:
                line = f"  - {name}: {dtype}"
                if sample_values > 0:
                    samples = self._fetch_samples(con, f.table_name, name, sample_values)
                    if samples:
                        formatted = ", ".join(_fmt_sample(v) for v in samples)
                        line += f" (e.g., {formatted})"
                lines.append(line)
            lines.append("")  # blank line between tables

        self._schema_cache = "\n".join(lines).rstrip()
        return self._schema_cache

    def execute(self, sql: str, timeout: float | None = None) -> Any:
        """Execute SQL against this session's DuckDB. Returns the result relation."""
        con = self.get_connection()
        self._last_query_at = time.time()

        timeout_s = timeout if timeout is not None else settings.sql_timeout_seconds
        timer = threading.Timer(timeout_s, con.interrupt)
        timer.start()
        try:
            return con.execute(sql)
        finally:
            timer.cancel()

    # --- Internals ---

    def _register_table(self, f: IngestedFile) -> None:
        """Create a VIEW over the Parquet file so it's queryable by table name."""
        path = f.parquet_path.as_posix()
        self._con.execute(
            f"CREATE OR REPLACE VIEW {f.table_name} AS "
            f"SELECT * FROM read_parquet('{path}')"
        )

    @staticmethod
    def _fetch_samples(
        con: duckdb.DuckDBPyConnection, table: str, column: str, n: int
    ) -> list[Any]:
        try:
            rows = con.execute(
                f'SELECT DISTINCT "{column}" FROM {table} '
                f'WHERE "{column}" IS NOT NULL LIMIT {n}'
            ).fetchall()
        except Exception:
            return []
        return [r[0] for r in rows]


# --- Global DatasetManager ---------------------------------------------------


class DatasetManager:
    """Manages per-session datasets with a global memory budget.

    Thread-safe. All public methods acquire the lock.
    """

    def __init__(self, global_budget: int = GLOBAL_BUDGET_BYTES) -> None:
        self._sessions: dict[str, SessionDataset] = {}
        self._lock = threading.Lock()
        self._global_budget = global_budget

    def get(self, session_id: str) -> SessionDataset | None:
        with self._lock:
            return self._sessions.get(session_id)

    def get_or_create(self, session_id: str, parquet_dir: Path) -> SessionDataset:
        with self._lock:
            if session_id not in self._sessions:
                self._sessions[session_id] = SessionDataset(
                    session_id=session_id,
                    parquet_dir=parquet_dir,
                )
            return self._sessions[session_id]

    def add_file(self, session_id: str, ingested: IngestedFile) -> None:
        """Register an ingested file with the session. Raises if over budget."""
        with self._lock:
            self._evict_idle_locked()

            ds = self._sessions.get(session_id)
            if ds is None:
                raise ValueError(f"Session {session_id} not found in DatasetManager")

            # Check global budget
            current_total = sum(d.estimated_memory for d in self._sessions.values())
            new_estimated = ingested.size_bytes * EXPANSION_FACTOR
            if current_total + new_estimated > self._global_budget:
                # Try evicting idle sessions to make room
                self._evict_idle_locked()
                current_total = sum(d.estimated_memory for d in self._sessions.values())
                if current_total + new_estimated > self._global_budget:
                    raise MemoryBudgetExceeded(
                        f"Server data capacity reached ({self._global_budget / 1024 / 1024:.0f} MB). "
                        f"Please try again later or use smaller files."
                    )

            ds.add_file(ingested)

    def close_session(self, session_id: str) -> None:
        """Close and remove a session's dataset entirely."""
        with self._lock:
            ds = self._sessions.pop(session_id, None)
            if ds:
                ds.close()

    @property
    def total_estimated_memory(self) -> float:
        with self._lock:
            return sum(d.estimated_memory for d in self._sessions.values())

    @property
    def active_sessions(self) -> int:
        with self._lock:
            return len(self._sessions)

    def _evict_idle_locked(self) -> None:
        """Close connections for idle sessions. Caller must hold the lock."""
        idle = [sid for sid, ds in self._sessions.items() if ds.is_idle]
        for sid in idle:
            self._sessions[sid].close()
            del self._sessions[sid]
            logger.info("Evicted idle session dataset: %s", sid)


class MemoryBudgetExceeded(Exception):
    """Raised when adding a file would exceed the global memory budget."""
    pass


# --- Module-level singleton --------------------------------------------------

_manager: DatasetManager | None = None


def get_dataset_manager() -> DatasetManager:
    global _manager
    if _manager is None:
        _manager = DatasetManager()
    return _manager


# --- Helper for sample formatting (shared with sql.py) -----------------------

def _fmt_sample(value: Any) -> str:
    if isinstance(value, str):
        if len(value) > 24:
            return f"'{value[:21]}...'"
        return f"'{value}'"
    return str(value)
