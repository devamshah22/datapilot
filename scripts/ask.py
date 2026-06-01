r"""Quick CLI for asking the agent a single question.

Usage from project root:
    .\.venv\Scripts\python.exe scripts\ask.py "How many orders are there in total?"

Follow-up across turns (same session):
    .\.venv\Scripts\python.exe scripts\ask.py --session abc123 "Now break that down by state"

If --session is omitted a new session is created and the id is printed at
the end so you can pass it on the next call.
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

# Make `import app` work when running this as a top-level script.
ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "backend"))

from app.agent.graph import record_query_after_run, run_agent  # noqa: E402


def main() -> int:
    parser = argparse.ArgumentParser(description="Ask the DataPilot agent a question.")
    parser.add_argument("question", nargs="+", help="The question text.")
    parser.add_argument(
        "--session",
        default=None,
        help="Existing session id to continue a conversation.",
    )
    args = parser.parse_args()
    question = " ".join(args.question)

    final, session_id = run_agent(question, session_id=args.session)
    record_query_after_run(session_id, final)

    print("=" * 70)
    print(f"Q: {question}")
    print(f"Session: {session_id}")
    print("-" * 70)
    print(f"Route: {final.get('route', '?')} -- {final.get('route_reason', '')}")

    attempts = final.get("previous_attempts", [])
    if attempts:
        print("-" * 70)
        print(f"Self-corrections: {len(attempts)} retry(ies) before final answer")
        for i, a in enumerate(attempts, 1):
            print(f"  attempt {i} error: {a['error'][:120]}")

    if final.get("sql"):
        print("-" * 70)
        print(f"Final SQL:\n{final['sql']}")
    print("-" * 70)

    if final.get("error"):
        print(f"ERROR: {final['error']}")
    else:
        print(f"Answer:\n{final.get('answer', '')}")
        if final.get("chart_spec"):
            chart = final["chart_spec"]
            traces = chart.get("data", [])
            chart_type = traces[0].get("type") if traces else "?"
            title = chart.get("layout", {}).get("title", "(no title)")
            print(f"\nChart: type={chart_type}  title={title!r}")
            print(json.dumps(chart, default=str)[:300] + " ...")
        elif final.get("route") in ("sql", "viz"):
            print(f"\n({final.get('row_count', 0)} rows, columns: {final.get('columns', [])})")
    print("=" * 70)
    return 0 if not final.get("error") else 2


if __name__ == "__main__":
    sys.exit(main())
