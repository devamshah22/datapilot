"""SQL sanitization — allowlist approach.

Only permits SELECT statements that reference registered session tables/views.
Rejects:
  - File paths (replacement scans like SELECT * FROM '/path/file.csv')
  - External functions (read_csv_auto, read_parquet, etc.)
  - DDL/DML (CREATE, DROP, INSERT, UPDATE, DELETE, ALTER)
  - Extension loading (INSTALL, LOAD)
  - ATTACH, COPY, PRAGMA
  - Unknown identifiers that look like file paths

The allowlist is: only the table names registered in the user's session.
"""
from __future__ import annotations

import re

# --- Patterns that are ALWAYS blocked regardless of context ---

# Statements that aren't SELECT/WITH
_BLOCKED_FIRST_TOKEN = re.compile(
    r"^\s*(insert|update|delete|drop|create|alter|attach|detach|copy|install|load|import|export|pragma)\b",
    re.IGNORECASE,
)

# File path patterns (replacement scans): any quoted string containing / or \ or .csv/.parquet etc.
_FILE_PATH_PATTERN = re.compile(
    r"""['"]([^'"]*[/\\][^'"]*|[^'"]*\.(csv|parquet|json|xlsx|xls|txt|tsv|db|sqlite|env|key|pem))['"]\s*""",
    re.IGNORECASE,
)

# Dangerous functions — even inside a SELECT these access the filesystem/network
_DANGEROUS_FUNCTIONS = re.compile(
    r"\b("
    r"read_csv_auto|read_csv|read_parquet|read_json|read_json_auto|"
    r"read_text|read_blob|read_ndjson|read_ndjson_auto|"
    r"parquet_scan|csv_scan|json_scan|"
    r"httpfs|http_get|http_post|"
    r"glob|list_files|"
    r"getenv|current_setting|"
    r"attach_database|load_extension|install_extension|"
    r"write_csv|write_parquet|"
    r"copy\s+.+\s+to\b"
    r")\s*\(",
    re.IGNORECASE,
)

# Extension-related keywords
_EXTENSION_PATTERN = re.compile(
    r"\b(install|load)\s+['\"]?\w+['\"]?",
    re.IGNORECASE,
)


def check_sql_safety(sql: str, allowed_tables: list[str] | None = None) -> str | None:
    """Validate SQL against the allowlist. Returns error message or None if safe.

    Parameters
    ----------
    sql : str
        The SQL query to validate.
    allowed_tables : list[str] | None
        Table/view names registered in the user's session. If provided,
        any FROM/JOIN reference not in this list is rejected.
    """
    sql_stripped = sql.strip().rstrip(";").strip()

    if not sql_stripped:
        return "Empty query."

    # 1. Must start with SELECT or WITH
    first_token = sql_stripped.split(None, 1)[0].lower()
    if first_token not in ("select", "with"):
        return f"Only SELECT/WITH queries are allowed; got '{first_token}'."

    # 2. Block explicitly dangerous first tokens (shouldn't pass #1, but defense in depth)
    if _BLOCKED_FIRST_TOKEN.search(sql_stripped):
        return "Statement type not allowed."

    # 3. Block file paths (replacement scans)
    path_match = _FILE_PATH_PATTERN.search(sql_stripped)
    if path_match:
        return f"File path references are not allowed in queries: '{path_match.group(1)[:50]}'"

    # 4. Block dangerous functions
    func_match = _DANGEROUS_FUNCTIONS.search(sql_stripped)
    if func_match:
        return f"Function '{func_match.group(1)}' is not allowed. Use table names instead."

    # 5. Block extension operations
    if _EXTENSION_PATTERN.search(sql_stripped):
        return "Extension operations are not allowed."

    # 6. Table allowlist check — if provided, verify FROM/JOIN references
    if allowed_tables is not None:
        unknown = _find_unknown_table_references(sql_stripped, allowed_tables)
        if unknown:
            return (
                f"Unknown table reference: '{unknown[0]}'. "
                f"Available tables: {allowed_tables}"
            )

    return None


def _find_unknown_table_references(sql: str, allowed: list[str]) -> list[str]:
    """Find table references in FROM/JOIN clauses that aren't in the allowed list.

    This is a heuristic parser — not a full SQL parser. It catches the common
    patterns: FROM table, JOIN table, FROM schema.table.
    """
    # Normalize: lowercase for comparison
    sql_lower = sql.lower()
    allowed_lower = {t.lower() for t in allowed}

    # Also allow common DuckDB built-ins that aren't tables
    allowed_lower.update({
        "generate_series", "range", "unnest", "information_schema",
        "pg_catalog", "duckdb_tables", "duckdb_columns",
    })

    # Find FROM and JOIN references
    # Patterns: FROM table, FROM table AS alias, JOIN table, JOIN table ON
    from_join_pattern = re.compile(
        r"(?:from|join)\s+([a-zA-Z_][a-zA-Z0-9_]*)",
        re.IGNORECASE,
    )

    unknown = []
    for match in from_join_pattern.finditer(sql):
        table_ref = match.group(1).lower()
        # Skip SQL keywords that might follow FROM/JOIN
        if table_ref in ("select", "where", "group", "order", "having", "limit",
                         "offset", "union", "intersect", "except", "lateral"):
            continue
        if table_ref not in allowed_lower:
            unknown.append(match.group(1))

    return unknown
