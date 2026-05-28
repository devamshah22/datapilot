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
