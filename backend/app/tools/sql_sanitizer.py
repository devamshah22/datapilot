"""SQL sanitization — blocks dangerous DuckDB functions that access the host filesystem.

DuckDB allows table functions like read_csv_auto(), read_parquet(), read_json(),
etc. inside SELECT statements. An LLM-prompt injection could exploit this to
read .env, local uploads, or any process-readable file.

This module provides a single function `check_sql_safety()` that returns an
error string if dangerous patterns are detected, or None if the query is safe.
"""
from __future__ import annotations

import re

# Patterns that indicate filesystem/network access from within SQL.
# These are DuckDB-specific table functions and pragmas.
_DANGEROUS_PATTERNS = re.compile(
    r"\b("
    r"read_csv_auto|read_csv|read_parquet|read_json|read_json_auto|"
    r"read_text|read_blob|"
    r"read_ndjson|read_ndjson_auto|"
    r"parquet_scan|csv_scan|json_scan|"
    r"httpfs|http_get|http_post|"
    r"copy\s+.+\s+to\b|"
    r"attach\b|"
    r"install\b|load\b|"
    r"pragma_database_list|pragma_table_info|"
    r"glob\s*\(|"
    r"list_files|"
    r"getenv|current_setting"
    r")\b",
    re.IGNORECASE,
)

# Allow read_parquet only when it references the session's own registered views.
# Since we create VIEWs over parquet files, legitimate queries never need to
# call read_parquet() directly — they query table/view names instead.


def check_sql_safety(sql: str) -> str | None:
    """Return an error message if the SQL contains dangerous patterns, else None.

    This is called BEFORE execution. It's a blocklist, not an allowlist —
    the tradeoff is that novel DuckDB functions we haven't listed could slip
    through. But it catches the known filesystem-access surface.
    """
    match = _DANGEROUS_PATTERNS.search(sql)
    if match:
        return (
            f"Query blocked: '{match.group()}' is not allowed. "
            "Queries must use table names, not file-access functions."
        )
    return None
