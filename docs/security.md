# Security model

This document describes what DataPilot defends against, what it does not,
and the design choices behind those decisions. It exists so that future-you
(and any reviewer or recruiter) has a clear, written reference instead of
having to reverse-engineer the threat model from code.

> Status: portfolio project. The security stance is appropriate for a
> non-authenticated single-tenant analytical service. A production
> deployment with multi-tenancy, write paths, or sensitive data would
> need additional controls.

## What this service is — and isn't

- **Is**: a read-only analytical interface over a single CSV file, exposed
  via a small FastAPI application. Single tenant. No authentication.
- **Is not**: a multi-tenant SaaS, an ETL pipeline, or a source-of-truth
  database front end.

The threat model below assumes a public deployment that any internet user
can hit.

## Threats and mitigations

| # | Threat                                                  | Mitigation                                                         | Where                                  |
| - | ------------------------------------------------------- | ------------------------------------------------------------------ | -------------------------------------- |
| 1 | SQL injection via the user's question                   | Question never becomes SQL directly — an LLM mediates with strict, structured prompts | `agent/nodes.py`, `agent/router.py`    |
| 2 | Mutating SQL (DROP, DELETE, INSERT, UPDATE, ALTER, etc.)| SQL tool rejects anything whose first token is not SELECT or WITH  | `tools/sql.py::SQLTool.execute`        |
| 3 | Reading arbitrary files via DuckDB (`read_csv`)         | Connection is in-memory; no files are added to the DB at runtime   | `tools/sql.py::SQLTool.__init__`       |
| 4 | Database persistence attacks                            | DuckDB is `:memory:` — process restart wipes everything            | `tools/sql.py::SQLTool.__init__`       |
| 5 | Result-size DoS (huge SELECT results)                   | Hard cap of 1000 rows on returned dataframes                       | `tools/sql.py::SQLTool.execute`        |
| 6 | Question-length DoS                                     | Pydantic `max_length=2000` on the request body                     | `schemas.py::AskRequest`               |
| 7 | Pathological compute (`SELECT * FROM range(10^12)`)     | Per-query timeout (default 10s) via `connection.interrupt()`       | `tools/sql.py::SQLTool.execute`        |
| 8 | LLM-quota / API DoS                                     | Per-IP rate limit on `/ask` (default 10/min)                       | `main.py` (slowapi)                    |
| 9 | Other endpoint DoS                                      | Per-IP rate limit on `/health`, `/schema` (default 60/min)         | `main.py` (slowapi)                    |
| 10| Cross-origin abuse                                      | CORS allow-list (configurable via `CORS_ALLOWED_ORIGINS`)          | `main.py`                              |
| 11| Sensitive PII surfacing                                 | Olist data is anonymized to start with; `refuse` route declines PII requests | dataset; `agent/router.py`     |
| 12| Forecasting / causal claims as "facts"                  | `refuse` route declines and explains                                | `agent/router.py`                      |

## On prompt injection

Prompt injection — a user crafting input that gets the LLM to ignore its
system prompt — is an open research problem. **It cannot be fully prevented
with text-level filtering.** Our stance is that consequences must be
constrained even when prompt isolation fails:

- Tricked LLM produces a `DELETE FROM orders` → SQL tool rejects it (rule 2).
- Tricked LLM tries to read `/etc/passwd` via DuckDB → no filesystem access (rule 3).
- Tricked LLM emits nonsense SQL → query fails or returns junk; user gets a bad answer; nothing is breached.
- Tricked LLM outputs profanity / off-topic text → reputation issue, not a security issue.

This is the standard mature posture: **defense is structural, not textual.**

## What we deliberately do NOT do

| Decision                                          | Rationale                                                                |
| ------------------------------------------------- | ------------------------------------------------------------------------ |
| Regex-based question filtering (block "DROP" etc.)| Easy to bypass, false positives on legitimate questions ("Did orders drop?"), false confidence |
| LLM-based input filtering ("is this malicious?")  | Adds latency and cost, false negatives, the structural defenses are stronger |
| Authentication / authorization                    | Out of scope for a single-tenant portfolio service. Add before any deployment with sensitive data |
| Per-user quotas                                   | No user concept yet. IP-based rate limit is the substitute               |
| Audit log of every question                       | FastAPI access logs are sufficient for the threat model. Add structured audit if scope grows |

## Settings reference

All safety knobs live in `.env` (or platform env vars). Defaults are
production-reasonable for a portfolio deployment.

```
SQL_TIMEOUT_SECONDS=10
RATE_LIMIT_ASK=10/minute
RATE_LIMIT_DEFAULT=60/minute
CORS_ALLOWED_ORIGINS=*           # comma-separated list, or '*' for any
MAX_AGENT_RETRIES=3
```

## What to revisit before any real deployment

In rough priority order:

1. **Tighten CORS** — replace `*` with the actual frontend origin.
2. **Authentication** — at minimum, an API key on `/ask` if the service is exposed beyond your local machine.
3. **Persistent rate-limit backend** — `slowapi` is in-memory; multiple worker processes need Redis-backed limiting.
4. **Structured audit log** — record `(timestamp, ip, question, route, sql)` to a separate log stream.
5. **Secrets rotation** — Groq / Gemini API keys should rotate periodically. Use a secrets manager, not `.env`, in production.
6. **Output PII scan** — although Olist is anonymized, future datasets may not be. Add a configurable PII scrubber on output.
