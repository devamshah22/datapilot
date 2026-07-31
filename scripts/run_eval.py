r"""Eval harness for DataPilot.

Runs all questions from evals/seed_questions.yaml against the live agent,
scores route correctness and execution success, and writes results to JSON.

Usage from project root:
    .\.venv\Scripts\python.exe scripts\run_eval.py

The harness creates a test session, uploads the Olist flat CSV (simulating a
real user upload), then runs every question against that session.

Scoring:
    1. Route match:  did the agent pick the expected route?
    2. Execution ok: for sql/viz, did the query run and return rows?
"""
from __future__ import annotations

import json
import sys
import time
import uuid
from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "backend"))

from app.agent.graph import record_query_after_run, run_agent  # noqa: E402
from app.tools.dataset_manager import get_dataset_manager  # noqa: E402
from app.tools.ingest import validate_and_convert  # noqa: E402


def setup_test_session() -> str:
    """Create a session with a test dataset uploaded (simulates real usage).

    Uses a sampled subset of the Olist data to stay under the 10MB upload cap.
    """
    session_id = f"eval{uuid.uuid4().hex[:12]}"
    csv_path = ROOT / "data" / "olist_v1_flat.csv"
    sample_path = ROOT / "data" / "olist_eval_sample.csv"

    if not csv_path.exists():
        print(f"ERROR: Test dataset not found at {csv_path}")
        print("Run: .\\.venv\\Scripts\\python.exe scripts\\load_olist.py")
        sys.exit(1)

    # Create a sample under the 10MB upload cap if it doesn't exist
    if not sample_path.exists():
        import pandas as pd
        print("Creating eval sample (first 25,000 rows)...")
        df = pd.read_csv(csv_path, nrows=25_000)
        df.to_csv(sample_path, index=False)
        size_mb = sample_path.stat().st_size / (1024 * 1024)
        print(f"  Sample: {len(df):,} rows, {size_mb:.1f} MB")

    parquet_dir = ROOT / "uploads" / session_id
    mgr = get_dataset_manager()
    mgr.get_or_create(session_id, parquet_dir)

    print(f"Uploading test dataset to session {session_id}...")
    with open(sample_path, "rb") as f:
        ingested = validate_and_convert(
            filename="orders.csv",
            file_data=f,
            output_dir=parquet_dir,
        )
    mgr.add_file(session_id, ingested)
    print(f"  Loaded table '{ingested.table_name}' ({ingested.row_count:,} rows, {len(ingested.columns)} cols)\n")

    return session_id


def load_questions() -> list[dict]:
    path = ROOT / "evals" / "seed_questions.yaml"
    with open(path, encoding="utf-8") as f:
        data = yaml.safe_load(f)
    return data["questions"]


def run_eval(delay_between: float = 3.0) -> dict:
    # Set up a session with data uploaded
    base_session = setup_test_session()

    questions = load_questions()
    results = []
    # Track sessions for follow-up chains
    prerequisite_sessions: dict[str, str] = {}

    total = 0
    route_correct = 0
    exec_ok = 0
    skipped = 0

    for idx, q in enumerate(questions):
        qid = q["id"]
        question_text = q["question"]
        expected_tool = q["expected_tool"]
        depends_on = q.get("depends_on")

        # Rate-limit delay (skip before the first question)
        if idx > 0 and delay_between > 0:
            time.sleep(delay_between)

        total += 1
        # Use the base session (which has data) unless this is a follow-up
        session_id = base_session
        session_id = base_session

        # For follow-ups, run the prerequisite first in the same session
        if depends_on and depends_on not in prerequisite_sessions:
            prereq = next((p for p in questions if p["id"] == depends_on), None)
            if prereq:
                try:
                    pre_final, _ = run_agent(prereq["question"], session_id=base_session)
                    record_query_after_run(base_session, pre_final)
                    prerequisite_sessions[depends_on] = base_session
                    time.sleep(delay_between)
                except Exception as e:
                    results.append({
                        "id": qid,
                        "question": question_text,
                        "expected_tool": expected_tool,
                        "status": "error",
                        "reason": f"prerequisite {depends_on} failed: {e}",
                    })
                    continue

        # Run the question
        t0 = time.time()
        try:
            final, sid = run_agent(question_text, session_id=session_id)
            record_query_after_run(sid, final)
            elapsed = time.time() - t0
        except Exception as e:
            results.append({
                "id": qid,
                "question": question_text,
                "expected_tool": expected_tool,
                "status": "error",
                "reason": str(e),
                "elapsed_s": time.time() - t0,
            })
            continue

        # Store session for potential follow-ups
        prerequisite_sessions[qid] = sid

        # Score
        actual_route = final.get("route", "")
        route_match = actual_route == expected_tool
        has_error = bool(final.get("error"))
        row_count = final.get("row_count", 0)

        if route_match:
            route_correct += 1

        execution_success = False
        if expected_tool in ("sql", "viz"):
            execution_success = not has_error and row_count > 0
            if execution_success:
                exec_ok += 1
        elif expected_tool == "python":
            # Python route: success = no error and produced output
            execution_success = not has_error and bool(final.get("python_output"))
            if execution_success:
                exec_ok += 1
        elif expected_tool in ("clarify", "refuse", "chat"):
            # No execution to check — success = route matched
            execution_success = route_match
            if execution_success:
                exec_ok += 1

        results.append({
            "id": qid,
            "question": question_text,
            "expected_tool": expected_tool,
            "actual_route": actual_route,
            "route_correct": route_match,
            "execution_ok": execution_success,
            "row_count": row_count,
            "error": final.get("error") or None,
            "sql": final.get("sql"),
            "answer_preview": (final.get("answer") or "")[:200],
            "elapsed_s": round(elapsed, 2),
        })

    summary = {
        "total": total,
        "skipped": skipped,
        "route_accuracy": round(route_correct / total * 100, 1) if total else 0,
        "execution_accuracy": round(exec_ok / total * 100, 1) if total else 0,
        "route_correct": route_correct,
        "exec_ok": exec_ok,
    }

    return {"summary": summary, "results": results}


def main() -> int:
    print("Running DataPilot eval suite...")
    print("=" * 60)

    output = run_eval()
    summary = output["summary"]

    # Write full results to JSON
    results_dir = ROOT / "evals" / "results"
    results_dir.mkdir(exist_ok=True)
    out_path = results_dir / "latest.json"
    with open(out_path, "w", encoding="utf-8") as f:
        json.dump(output, f, indent=2, default=str)

    # Print summary
    print(f"\nResults written to: {out_path}")
    print(f"\n{'='*60}")
    print(f"  Total questions:      {summary['total']}")
    print(f"  Skipped (no tool):    {summary['skipped']}")
    print(f"  Route accuracy:       {summary['route_correct']}/{summary['total']} = {summary['route_accuracy']}%")
    print(f"  Execution accuracy:   {summary['exec_ok']}/{summary['total']} = {summary['execution_accuracy']}%")
    print(f"{'='*60}")

    # Per-question breakdown
    print(f"\n{'ID':<12} {'Expected':<10} {'Actual':<10} {'Route':<6} {'Exec':<6} {'Time':<6}")
    print("-" * 60)
    for r in output["results"]:
        if r.get("status") == "skipped":
            print(f"{r['id']:<12} {r['expected_tool']:<10} {'—':<10} {'SKIP':<6} {'—':<6} {'—':<6}")
            continue
        if r.get("status") == "error":
            print(f"{r['id']:<12} {r['expected_tool']:<10} {'ERR':<10} {'—':<6} {'—':<6} {'—':<6}")
            continue
        route_mark = "OK" if r["route_correct"] else "MISS"
        exec_mark = "OK" if r["execution_ok"] else "FAIL"
        elapsed = f"{r['elapsed_s']:.1f}s"
        print(f"{r['id']:<12} {r['expected_tool']:<10} {r['actual_route']:<10} {route_mark:<6} {exec_mark:<6} {elapsed:<6}")

    return 0


if __name__ == "__main__":
    sys.exit(main())
