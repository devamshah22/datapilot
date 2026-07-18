r"""Eval harness for DataPilot.

Runs all questions from evals/seed_questions.yaml against the live agent,
scores route correctness and execution success, and writes results to JSON.

Usage from project root:
    .\.venv\Scripts\python.exe scripts\run_eval.py

Outputs:
    evals/results/latest.json   — full per-question results
    stdout                      — summary table with accuracy %

Scoring criteria:
    1. Route match:  did the agent pick the expected route? (sql/viz/clarify/refuse)
    2. Execution ok: for sql/viz, did the query run without error?
    3. Non-empty:    for sql/viz, did the query return at least 1 row?

Follow-up questions run their prerequisite in the same session first.
Python-category questions are skipped (pandas tool not built yet).
"""
from __future__ import annotations

import json
import sys
import time
from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "backend"))

from app.agent.graph import record_query_after_run, run_agent  # noqa: E402


def load_questions() -> list[dict]:
    path = ROOT / "evals" / "seed_questions.yaml"
    with open(path, encoding="utf-8") as f:
        data = yaml.safe_load(f)
    return data["questions"]


def run_eval(delay_between: float = 3.0) -> dict:
    questions = load_questions()
    results = []
    # Track sessions for follow-up chains
    prerequisite_sessions: dict[str, str] = {}  # question_id -> session_id

    total = 0
    route_correct = 0
    exec_ok = 0
    skipped = 0

    for idx, q in enumerate(questions):
        qid = q["id"]
        question_text = q["question"]
        expected_tool = q["expected_tool"]
        category = q["category"]
        depends_on = q.get("depends_on")

        # Skip python questions (tool not built)
        if expected_tool == "python":
            results.append({
                "id": qid,
                "question": question_text,
                "expected_tool": expected_tool,
                "status": "skipped",
                "reason": "pandas tool not implemented yet",
            })
            skipped += 1
            continue

        # Rate-limit delay (skip before the first question)
        if idx > 0 and delay_between > 0:
            time.sleep(delay_between)

        total += 1
        session_id = None

        # For follow-ups, run the prerequisite first if not already done
        if depends_on:
            if depends_on in prerequisite_sessions:
                session_id = prerequisite_sessions[depends_on]
            else:
                # Find and run the prerequisite question
                prereq = next((p for p in questions if p["id"] == depends_on), None)
                if prereq:
                    try:
                        pre_final, pre_sid = run_agent(prereq["question"])
                        record_query_after_run(pre_sid, pre_final)
                        prerequisite_sessions[depends_on] = pre_sid
                        session_id = pre_sid
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
        elif expected_tool in ("clarify", "refuse"):
            # For clarify/refuse, success = route matched (no execution to check)
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
