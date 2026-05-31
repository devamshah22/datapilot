"""Tests for the SQL tool.

These exercise the DuckDB wrapper independently of any LLM call so they
are fast and deterministic.
"""
from __future__ import annotations

import pandas as pd

from app.tools.sql import SQLTool


def test_loads_and_counts(sql_tool: SQLTool) -> None:
    result = sql_tool.execute(f"SELECT COUNT(*) AS n FROM {sql_tool.table_name}")
    assert result.ok, result.error
    assert result.dataframe is not None
    assert int(result.dataframe.iloc[0]["n"]) > 0


def test_distinct_orders_is_below_total_rows(sql_tool: SQLTool) -> None:
    """The flat table has multiple rows per order; verify our schema-literacy
    intuition (used to teach the LLM via the system prompt) actually holds."""
    total = sql_tool.execute(f"SELECT COUNT(*) AS n FROM {sql_tool.table_name}")
    distinct = sql_tool.execute(
        f"SELECT COUNT(DISTINCT order_id) AS n FROM {sql_tool.table_name}"
    )
    assert total.ok and distinct.ok
    assert int(distinct.dataframe.iloc[0]["n"]) < int(total.dataframe.iloc[0]["n"])


def test_timestamp_column_is_typed(sql_tool: SQLTool) -> None:
    """Regression: the flat CSV used to be loaded with VARCHAR timestamps,
    which broke every time-series question. Lock in the typed cast."""
    result = sql_tool.execute(
        "SELECT typeof(order_purchase_timestamp) AS t FROM orders LIMIT 1"
    )
    assert result.ok, result.error
    assert "TIMESTAMP" in result.dataframe.iloc[0]["t"].upper()


def test_rejects_mutations(sql_tool: SQLTool) -> None:
    for sql in ["DELETE FROM orders", "UPDATE orders SET price = 0", "DROP TABLE orders"]:
        result = sql_tool.execute(sql)
        assert not result.ok
        assert "SELECT" in (result.error or "")


def test_returns_error_on_bad_sql(sql_tool: SQLTool) -> None:
    result = sql_tool.execute("SELECT FROM orders")  # syntax error
    assert not result.ok
    assert result.error


def test_schema_summary_includes_sample_values_by_default(sql_tool: SQLTool) -> None:
    """Sample values let the model judge which column is human-readable
    (e.g., product_category_en vs product_category_name) without us
    hard-coding rules per dataset."""
    summary = sql_tool.schema_summary()
    assert "Table: orders" in summary
    assert "Columns:" in summary
    # Every column should have an "(e.g., ...)" annotation
    for line in summary.splitlines():
        if line.startswith("  - "):
            assert "(e.g.," in line, f"missing sample on: {line}"


def test_schema_summary_omits_samples_when_requested(sql_tool: SQLTool) -> None:
    summary = sql_tool.schema_summary(sample_values=0)
    assert "(e.g.," not in summary


def test_schema_summary_caches_per_sample_count(sql_tool: SQLTool) -> None:
    """Different sample-count requests must not collide in the cache."""
    a = sql_tool.schema_summary(sample_values=0)
    b = sql_tool.schema_summary(sample_values=2)
    assert a != b
    assert sql_tool.schema_summary(sample_values=0) == a  # idempotent
    assert sql_tool.schema_summary(sample_values=2) == b


def test_query_timeout_cancels_pathological_query(sql_tool: SQLTool) -> None:
    """A query that would take far longer than the timeout must be cancelled
    and surface a clean timeout error rather than hanging indefinitely."""
    # generate_series of 10 billion rows would never finish; interrupted fast
    huge = "SELECT COUNT(*) FROM range(0, 10000000000)"
    result = sql_tool.execute(huge, timeout=1.0)
    assert not result.ok
    assert "timeout" in (result.error or "").lower()
