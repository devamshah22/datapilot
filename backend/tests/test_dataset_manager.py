"""Tests for the per-session DatasetManager.

These create real Parquet files and DuckDB connections but don't touch
any network, LLM, or external service.
"""
from __future__ import annotations

import io
from pathlib import Path

import pytest

from app.tools.dataset_manager import (
    DatasetManager,
    MemoryBudgetExceeded,
    SessionDataset,
)
from app.tools.ingest import validate_and_convert


def _ingest_csv(tmp_path: Path, filename: str = "test.csv", rows: int = 50) -> "IngestedFile":
    """Helper: create a small CSV and ingest it."""
    from app.tools.ingest import IngestedFile

    header = "id,name,value\n"
    body = "".join(f"{i},item_{i},{i * 10.5}\n" for i in range(rows))
    data = io.BytesIO((header + body).encode())
    return validate_and_convert(filename, data, tmp_path)


# --- SessionDataset ----------------------------------------------------------


def test_session_dataset_registers_and_queries(tmp_path: Path) -> None:
    ingested = _ingest_csv(tmp_path)
    ds = SessionDataset(session_id="s1", parquet_dir=tmp_path)
    ds.add_file(ingested)

    result = ds.execute(f"SELECT COUNT(*) AS n FROM {ingested.table_name}")
    row = result.fetchone()
    assert row[0] == 50


def test_session_dataset_schema_summary(tmp_path: Path) -> None:
    ingested = _ingest_csv(tmp_path)
    ds = SessionDataset(session_id="s1", parquet_dir=tmp_path)
    ds.add_file(ingested)

    schema = ds.schema_summary()
    assert "test" in schema
    assert "50 rows" in schema
    assert "id" in schema
    assert "name" in schema
    assert "value" in schema


def test_session_dataset_multi_file(tmp_path: Path) -> None:
    f1 = _ingest_csv(tmp_path, "sales.csv", rows=10)
    f2 = _ingest_csv(tmp_path, "customers.csv", rows=20)
    ds = SessionDataset(session_id="s1", parquet_dir=tmp_path)
    ds.add_file(f1)
    ds.add_file(f2)

    assert len(ds.files) == 2
    assert set(ds.table_names) == {"sales", "customers"}
    # Can query both
    r1 = ds.execute("SELECT COUNT(*) FROM sales").fetchone()[0]
    r2 = ds.execute("SELECT COUNT(*) FROM customers").fetchone()[0]
    assert r1 == 10
    assert r2 == 20


def test_session_dataset_close_and_reopen(tmp_path: Path) -> None:
    ingested = _ingest_csv(tmp_path)
    ds = SessionDataset(session_id="s1", parquet_dir=tmp_path)
    ds.add_file(ingested)
    ds.close()
    # After close, get_connection reopens and re-registers tables
    result = ds.execute(f"SELECT COUNT(*) FROM {ingested.table_name}")
    assert result.fetchone()[0] == 50


# --- DatasetManager ----------------------------------------------------------


def test_manager_add_file_and_query(tmp_path: Path) -> None:
    mgr = DatasetManager()
    mgr.get_or_create("s1", tmp_path)
    ingested = _ingest_csv(tmp_path)
    mgr.add_file("s1", ingested)

    ds = mgr.get("s1")
    assert ds is not None
    result = ds.execute("SELECT COUNT(*) FROM test")
    assert result.fetchone()[0] == 50


def test_manager_budget_exceeded(tmp_path: Path) -> None:
    # Tiny budget: 100 bytes — any real file will exceed it
    mgr = DatasetManager(global_budget=100)
    mgr.get_or_create("s1", tmp_path)
    ingested = _ingest_csv(tmp_path)

    with pytest.raises(MemoryBudgetExceeded):
        mgr.add_file("s1", ingested)


def test_manager_close_session(tmp_path: Path) -> None:
    mgr = DatasetManager()
    mgr.get_or_create("s1", tmp_path)
    ingested = _ingest_csv(tmp_path)
    mgr.add_file("s1", ingested)
    mgr.close_session("s1")
    assert mgr.get("s1") is None
