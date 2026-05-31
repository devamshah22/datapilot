"""DuckDB-backed SQL execution.

A thin wrapper around an in-memory DuckDB connection that:
  - loads the dataset CSV into a single named table at startup
  - exposes a `schema_summary()` for prompting the LLM
  - exposes `execute(sql)` returning a small typed result

We deliberately keep the table name configurable so we can change the
"shape" presented to the agent (e.g., switch from flat to multi-table)
without editing the agent code.

Safety:
  - SELECT/WITH only — DDL and DML are rejected at the wrapper level.
  - In-memory database, no persistence.
  - Per-query timeout via DuckDB's thread-safe ``connection.interrupt()``.
  - Result row cap.
"""
from __future__ import annotations

import threading
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import duckdb
import pandas as pd

from app.config import settings


# Columns that DuckDB's CSV auto-detection reads as VARCHAR but which we
# know to be timestamps in the Olist v1 flat dataset. Forcing the cast at
# load time makes time-series questions work without per-query gymnastics.
OLIST_V1_TIMESTAMP_COLUMNS = {
    "order_purchase_timestamp": "TIMESTAMP",
    "order_approved_at": "TIMESTAMP",
    "order_delivered_carrier_date": "TIMESTAMP",
    "order_delivered_customer_date": "TIMESTAMP",
    "order_estimated_delivery_date": "TIMESTAMP",
    "shipping_limit_date": "TIMESTAMP",
    "review_creation_date": "TIMESTAMP",
}


@dataclass
class SQLResult:
    """Output of a SQL execution.

    Either `dataframe` is set (success) or `error` is set (failure). Never both.
    """
    sql: str
    dataframe: pd.DataFrame | None = None
    error: str | None = None
    row_count: int = 0
    columns: list[str] = field(default_factory=list)

    @property
    def ok(self) -> bool:
        return self.error is None


class SQLTool:
    """In-memory DuckDB with the dataset loaded as a single table."""

    def __init__(
        self,
        csv_path: Path | None = None,
        table_name: str | None = None,
        column_type_overrides: dict[str, str] | None = None,
    ) -> None:
        self.csv_path = Path(csv_path or settings.dataset_path)
        self.table_name = table_name or settings.dataset_table_name
        self.column_type_overrides = (
            column_type_overrides
            if column_type_overrides is not None
            else dict(OLIST_V1_TIMESTAMP_COLUMNS)
        )

        if not self.csv_path.exists():
            raise FileNotFoundError(
                f"Dataset CSV not found at {self.csv_path}. "
                "Run `python scripts/load_olist.py` first."
            )

        self.con = duckdb.connect(":memory:")

        # Build the `types={...}` arg for read_csv_auto so timestamp columns
        # are parsed correctly instead of being dumped to VARCHAR.
        if self.column_type_overrides:
            types_struct = ", ".join(
                f"'{col}': '{dtype}'"
                for col, dtype in self.column_type_overrides.items()
            )
            types_clause = f", types={{{types_struct}}}"
        else:
            types_clause = ""

        self.con.execute(
            f"CREATE TABLE {self.table_name} AS "
            f"SELECT * FROM read_csv_auto('{self.csv_path.as_posix()}'{types_clause})"
        )
        # Cache: (sample_values) -> rendered schema string
        self._schema_cache: dict[int, str] = {}

    def schema_summary(self, sample_values: int = 2) -> str:
        """Return a compact description of the table for prompting.

        With `sample_values` > 0, each column is annotated with that many
        distinct example values. This is what lets the agent prefer
        ``product_category_en`` ("housewares") over ``product_category_name``
        ("utilidades_domesticas") without us hard-coding a rule per dataset.

        Format:
            Table: orders (112650 rows)
            Columns:
              - order_id: VARCHAR (e.g., 'e481f51c...', '53cdb2fc...')
              - price: DOUBLE (e.g., 29.99, 158.0)
              ...
        """
        if sample_values in self._schema_cache:
            return self._schema_cache[sample_values]

        n_rows = self.con.execute(
            f"SELECT COUNT(*) FROM {self.table_name}"
        ).fetchone()[0]
        cols = self.con.execute(f"DESCRIBE {self.table_name}").fetchall()
        # DESCRIBE returns: (column_name, column_type, null, key, default, extra)

        lines = [f"Table: {self.table_name} ({n_rows:,} rows)", "Columns:"]
        for name, dtype, *_ in cols:
            line = f"  - {name}: {dtype}"
            if sample_values > 0:
                samples = self._fetch_sample_values(name, sample_values)
                if samples:
                    formatted = ", ".join(self._fmt_sample(v) for v in samples)
                    line += f" (e.g., {formatted})"
            lines.append(line)

        result = "\n".join(lines)
        self._schema_cache[sample_values] = result
        return result

    def _fetch_sample_values(self, column: str, n: int) -> list[Any]:
        """Return up to ``n`` distinct non-null sample values for a column."""
        # Quote the column name to be safe with reserved words / casing.
        try:
            rows = self.con.execute(
                f"SELECT DISTINCT \"{column}\" FROM {self.table_name} "
                f"WHERE \"{column}\" IS NOT NULL "
                f"LIMIT {n}"
            ).fetchall()
        except Exception:
            return []
        return [r[0] for r in rows]

    @staticmethod
    def _fmt_sample(value: Any) -> str:
        """Render a sample value compactly for the schema prompt."""
        if isinstance(value, str):
            # Truncate long string values (UUIDs, free text) so the prompt
            # stays small and readable.
            if len(value) > 24:
                return f"'{value[:21]}...'"
            return f"'{value}'"
        return str(value)

    def execute(
        self,
        sql: str,
        max_rows: int = 1000,
        timeout: float | None = None,
    ) -> SQLResult:
        """Run a SELECT and return at most ``max_rows`` rows.

        Mutating statements are rejected (this is a read-only analytical tool).
        Pathological queries are killed via ``connection.interrupt()`` after
        ``timeout`` seconds (defaults to ``settings.sql_timeout_seconds``).
        """
        sql_stripped = sql.strip().rstrip(";").strip()
        first_token = sql_stripped.split(None, 1)[0].lower() if sql_stripped else ""

        if first_token not in {"select", "with"}:
            return SQLResult(
                sql=sql,
                error=(
                    f"Only SELECT/WITH queries are allowed; got '{first_token or '<empty>'}'."
                ),
            )

        timeout_s = timeout if timeout is not None else settings.sql_timeout_seconds
        timer = threading.Timer(timeout_s, self.con.interrupt)
        timer.start()
        try:
            df = self.con.execute(sql_stripped).fetch_df()
        except duckdb.duckdb.InterruptException:
            return SQLResult(
                sql=sql,
                error=f"Query exceeded {timeout_s:.0f}s timeout and was cancelled.",
            )
        except Exception as e:  # noqa: BLE001 — surfacing the message to the agent
            return SQLResult(sql=sql, error=f"{type(e).__name__}: {e}")
        finally:
            timer.cancel()

        truncated = False
        total_rows = len(df)
        if total_rows > max_rows:
            df = df.head(max_rows)
            truncated = True

        return SQLResult(
            sql=sql_stripped,
            dataframe=df,
            row_count=total_rows + (0 if not truncated else 0),
            columns=list(df.columns),
        )

    def close(self) -> None:
        self.con.close()


# Module-level singleton — created lazily so import is cheap and tests can override.
_tool: SQLTool | None = None


def get_sql_tool() -> SQLTool:
    global _tool
    if _tool is None:
        _tool = SQLTool()
    return _tool
