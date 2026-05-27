# DataPilot

Conversational data analysis for CSV files. Ask questions in plain English; an LLM agent picks the right tool (SQL, Python, or visualization), executes it in a sandbox, validates the result, and returns the answer with the code that produced it.

> **Status:** 🚧 Early development. Building in public. See [docs/PROGRESS.md](docs/PROGRESS.md) for week-by-week updates.

## Why this exists

Text-to-SQL tools are common. DataPilot goes further:

- **Multi-tool routing** — the agent chooses between SQL (DuckDB), Python (sandboxed pandas), and Plotly visualizations based on the question.
- **Self-correction** — when execution fails, the agent reasons about *why* and retries with a fix, up to a bounded number of attempts.
- **Output validation** — every result is sanity-checked (row counts, types, plausibility) before being shown. Reduces silent hallucinations.
- **Transparent reasoning** — every answer ships with the code that produced it and the agent's tool-choice rationale.
- **In-session memory** — follow-up questions inherit context from earlier turns without poisoning later ones.

## Architecture (planned)

```
User Query
   │
   ▼
[Router]   ── decides: SQL? Python? Viz? Clarify?
   │
   ▼
[Executor] ── runs in sandbox, captures output and errors
   │
   ▼
[Validator] ── sanity-checks output before returning
   │
   ▼
[Self-Correction] ── on failure, diagnose root cause and retry (≤3)
   │
   ▼
[Memory]    ── update structured session state
   │
   ▼
Response: answer + code + chart + reasoning trace
```

See [docs/architecture.md](docs/architecture.md) for the full design (coming soon).

## Tech stack

| Layer        | Choice                              | Why                                    |
| ------------ | ----------------------------------- | -------------------------------------- |
| LLM          | Google Gemini 2.5 Flash (free tier) | Strong tool-calling, $0 dev cost       |
| Agent        | LangGraph                           | State machines map cleanly to design   |
| SQL engine   | DuckDB                              | Runs SQL on CSVs in-process, fast      |
| Sandbox exec | E2B (planned)                       | Isolated Python execution              |
| Charts       | Plotly                              | LLM-friendly JSON spec, web-renderable |
| Backend      | FastAPI                             | Same language as the agent             |
| Frontend     | Next.js + shadcn/ui                 | Professional polish with low effort    |

## Repository layout

```
datapilot/
├── backend/          FastAPI app, agent, tools
│   └── app/
│       ├── agent/    LangGraph state machine
│       └── tools/    SQL / Python / viz tools
├── frontend/         Next.js UI (later)
├── evals/            Eval question set + harness
├── data/             Dev CSVs (gitignored)
└── docs/             Architecture, blog drafts
```

## Local development

> Requires Python 3.10+, Node 20+, Git.

```powershell
# 1. Clone and enter
git clone https://github.com/devamshah22/datapilot.git
cd datapilot

# 2. Set up env
copy .env.example .env
# Edit .env and add your GEMINI_API_KEY (free at https://aistudio.google.com)

# 3. Backend
python -m venv .venv
.\.venv\Scripts\Activate.ps1
pip install -r backend/requirements.txt
```

Frontend setup will be added once the backend MVP is working.

## Roadmap

- [ ] Week 1 — Project scaffold, eval seed set, CSV-to-DuckDB plumbing
- [ ] Week 2 — LangGraph agent skeleton, sandboxed Python tool
- [ ] Week 3 — Visualization tool, structured router prompt
- [ ] Week 4 — Self-correction loop and output validator
- [ ] Week 5 — In-session memory, auto-EDA on upload
- [ ] Week 6 — Eval suite, failure-mode analysis
- [ ] Week 7 — Deployment, demo video, blog post

## License

MIT — see [LICENSE](LICENSE).
