# DataPilot

**Live demo:** [datapilot-theta.vercel.app](https://datapilot-theta.vercel.app)

Upload any CSV or Excel file and ask questions in plain English. The AI agent autonomously picks between SQL, Python, and visualization tools — then executes, validates, self-corrects on error, and returns the answer with a chart.

## What it does

1. **Upload** your CSV or Excel file (up to 10MB)
2. **Ask** questions in natural language
3. **Get answers** as text, tables, or interactive charts
4. **Follow up** — the agent remembers context across turns

The agent decides *how* to answer each question:

| Route | When | Example |
|-------|------|---------|
| SQL | Aggregations, filters, rankings | "Which category has the highest revenue?" |
| Python | Stats, correlations, clustering | "Find outliers in price" |
| Visualization | Charts and graphs | "Show monthly revenue as a line chart" |
| Chat | General questions, explanations | "What columns do I have?" |
| Clarify | Ambiguous questions | "How are sales doing?" → asks for specifics |
| Refuse | Forecasting, causal claims | "What will revenue be next quarter?" |

## Tech stack

| Layer | Choice |
|-------|--------|
| LLM | Groq (llama-3.3-70b) / Gemini — pluggable via env var |
| Agent | LangGraph (state machine with routing + self-correction) |
| SQL engine | DuckDB (queries Parquet files on disk, low RAM) |
| Python sandbox | Docker container (local) / subprocess fallback (Cloud Run) |
| Charts | Plotly (LLM picks chart type, frontend renders) |
| Backend | FastAPI + Supabase (Postgres + Storage + Auth) |
| Frontend | Next.js 16 + shadcn/ui + Tailwind |
| Deployment | Google Cloud Run (backend) + Vercel (frontend) |

## Features

- **Multi-tool autonomous agent** — routes to SQL, Python, viz, chat, clarify, or refuse
- **Self-correction** — retries failed queries with diagnosis (up to 3 attempts)
- **Docker-sandboxed Python** — LLM-generated code runs with no network, capped memory/CPU
- **SQL sanitization** — blocks filesystem-access functions (read_csv_auto, etc.)
- **File upload** — CSV/Excel → lossless Parquet compression → persistent in Supabase Storage
- **Multi-user auth** — Supabase Auth (Google OAuth + email/password), JWT validation, RLS
- **Session persistence** — conversations survive restarts, resumable across days
- **Interactive charts** — Plotly with fullscreen modal, download as PNG, per-chart targeting
- **Dark/light theme** — system default with manual toggle
- **Rate limiting, CORS, query timeout** — production-grade safety

## Architecture

```
User Question
      │
      ▼
[Router] ── decides: SQL? Python? Viz? Chat? Clarify? Refuse?
      │
      ▼
[Tool Executor] ── SQL (DuckDB) / Python (Docker) / LLM direct
      │
      ▼
[Validator] ── sanity-checks result (zero rows? all null?)
      │
      ▼
[Self-Correction] ── on failure: diagnose + retry (≤3 attempts)
      │
      ▼
[Memory] ── update session context for follow-ups
      │
      ▼
Response (answer + chart)
```

## Security

- SQL sanitizer blocks 20+ DuckDB filesystem-access functions
- Docker sandbox: `--network=none`, `--memory=256m`, `--cpus=1`, `--cap-drop=ALL`
- Supabase Auth with JWT validation (ES256 via JWKS + HS256 fallback)
- Per-user session scoping with ownership verification
- RLS policies on all tables (defense-in-depth)
- Rate limiting (10/min on /ask, 60/min elsewhere)
- File upload caps: 10MB/file, 500k rows, 200 columns

See [docs/security.md](docs/security.md) for the full threat model.

## Local development

> Requires Python 3.10+, Node 20+, Git, Docker (for Python sandbox).

```powershell
# Clone
git clone https://github.com/devamshah22/datapilot.git
cd datapilot

# Backend
copy .env.example .env   # fill in your API keys
python -m venv .venv
.\.venv\Scripts\Activate.ps1
pip install -r backend/requirements.txt
.\.venv\Scripts\python.exe -m uvicorn app.main:app --reload --app-dir backend

# Frontend (separate terminal)
cd frontend
npm install
npm run dev
```

## Deployment

- **Backend:** Google Cloud Run (auto-deploys from `main` branch)
- **Frontend:** Vercel (auto-deploys from `main` branch, `frontend/` root)
- **Database:** Supabase (Postgres + Storage + Auth)

## License

MIT — see [LICENSE](LICENSE).
