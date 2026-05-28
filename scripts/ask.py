"""Quick CLI for asking the agent a single question.

Usage from project root:
    .\.venv\Scripts\python.exe scripts\ask.py "How many orders are there in total?"

Avoids the overhead of starting a FastAPI server during dev. Useful for
manual eval-question runs.
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

# Make `import app` work when running this as a top-level script.
ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "backend"))

from app.agent.graph import run_agent  # noqa: E402


def main() -> int:
    if len(sys.argv) < 2:
        print('Usage: python -m scripts.ask "your question"')
        return 1

    question = " ".join(sys.argv[1:])
    final = run_agent(question)

    print("=" * 70)
    print(f"Q: {question}")
    print("-" * 70)
    print(f"SQL:\n{final.get('sql', '')}")
    print("-" * 70)
    if final.get("error"):
        print(f"ERROR: {final['error']}")
    else:
        print(f"Answer:\n{final.get('answer', '')}")
        print(f"\n({final.get('row_count', 0)} rows, columns: {final.get('columns', [])})")
    print("=" * 70)
    return 0 if not final.get("error") else 2


if __name__ == "__main__":
    sys.exit(main())
