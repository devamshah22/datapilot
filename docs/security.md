# Security model

This document describes DataPilot's security posture as of the current
implementation. It covers threats, mitigations, and known limitations.

> **Architecture:** Multi-user SaaS with Supabase Auth (JWT), per-user
> session scoping, file uploads persisted in Supabase Storage, and
> LLM-generated code execution in Docker sandboxes.

## Authentication & Authorization

| Control | Implementation |
|---------|---------------|
| Authentication | Supabase Auth JWT validated by backend middleware on every request |
| Session ownership | `_verify_session_ownership` checks user_id before read/write/delete |
| RLS (defense-in-depth) | Postgres Row Level Security policies on sessions, messages, query_memories |
| Public endpoints | Only /health, /docs, /openapi.json, /redoc skip auth |

## SQL Execution Safety

| Threat | Mitigation |
|--------|-----------|
| Mutation (DROP, DELETE, INSERT) | First-token check: only SELECT/WITH allowed |
| Filesystem access (read_csv_auto, read_parquet, etc.) | `sql_sanitizer.py` blocks 20+ dangerous DuckDB functions via regex |
| Pathological compute | Per-query timeout (10s) via DuckDB `connection.interrupt()` |
| Result-size DoS | Hard cap of 1000 rows returned |
| In-memory database | No persistence; restart wipes DuckDB state |

**Note:** The sanitizer is a blocklist. Novel DuckDB functions we haven't
listed could theoretically bypass it. The structural defense (VIEWs over
registered Parquet only, no raw file paths in user-facing queries) provides
a second layer.

## Python Code Execution (Docker Sandbox)

| Control | Setting |
|---------|---------|
| Network | `--network=none` |
| Memory | `--memory=256m` |
| CPU | `--cpus=1` |
| PIDs | `--pids-limit=64` |
| Filesystem | `--read-only` + tmpfs for /tmp (50MB) |
| Privileges | `--cap-drop=ALL`, `--security-opt=no-new-privileges` |
| User | Non-root (`sandbox` user inside container) |
| Timeout | 15s subprocess kill |
| Import whitelist | Validated before execution; only pandas/numpy/scipy/sklearn/math/datetime |
| Data mount | Read-only bind mount of session's Parquet files only |

## File Upload Safety

| Control | Limit |
|---------|-------|
| File size | 10 MB per file (raw upload bytes) |
| Rows | 500,000 max |
| Columns | 200 max |
| Files per batch | 5 |
| Global memory budget | 250 MB estimated across all sessions |
| Idle eviction | DuckDB connections closed after 15 min |
| Allowed types | .csv, .xlsx, .xls only |
| Storage persistence | Supabase Storage (private bucket) |

## Session & Data Lifecycle

| Event | What happens |
|-------|-------------|
| Upload | CSV/Excel → Parquet locally → Supabase Storage → DuckDB VIEW |
| Query | DuckDB streams from Parquet on disk (low RAM) |
| Idle (15 min) | DuckDB connection closed; Parquet stays on disk + storage |
| Server restart | Parquet re-downloaded from Supabase Storage on next access |
| Session delete | Storage files deleted FIRST → in-memory closed → DB records cascaded |
| Partial delete failure | User receives "partial" status; files may be orphaned (logged) |

## API Rate Limiting

| Endpoint | Limit |
|----------|-------|
| /ask | 10/minute per IP |
| All others | 60/minute per IP |
| Backed by | slowapi (in-process); for multi-worker, add Redis |

## CORS

Default: `http://localhost:3000` (explicit origin with credentials).
Configure via `CORS_ALLOWED_ORIGINS` env var for production domains.

## Known Limitations

1. **SQL sanitizer is a blocklist** — novel DuckDB functions could bypass it
2. **Rate limiter is in-process** — multiple workers need Redis-backed limiting
3. **File deletion is best-effort** — storage errors are logged, not retried
4. **No per-user storage quotas** — global budget only, not per-user
5. **Docker required for Python tool** — falls back to error if Docker unavailable

## Pre-deployment Checklist

1. Set `CORS_ALLOWED_ORIGINS` to your production frontend URL
2. Ensure `SUPABASE_JWT_SECRET` is the correct signing secret
3. Verify Docker is available on the deploy host
4. Set appropriate rate limits for expected traffic
5. Consider Redis-backed rate limiting for multi-worker deploys
6. Monitor Supabase Storage usage against the 1GB free tier
7. Set up log aggregation for security events
