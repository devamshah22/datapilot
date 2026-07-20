"""One-off localhost integration test for DataPilot API."""
from __future__ import annotations

import json
import uuid
from pathlib import Path

import httpx

BASE = "http://localhost:8000"
CSV_PATH = Path(__file__).parent / "tmp_test_orders.csv"


def main() -> None:
    CSV_PATH.write_text(
        "order_id,state,revenue\n"
        + "\n".join(f"o{i},SP,{100 + i}" for i in range(1, 101)),
        encoding="utf-8",
    )
    print(f"Test CSV: {CSV_PATH.stat().st_size / 1024:.1f} KB")

    session_id = uuid.uuid4().hex
    print(f"Session: {session_id}")

    with httpx.Client(timeout=120.0) as client:
        print("\n=== /health ===")
        print(json.dumps(client.get(f"{BASE}/health").json(), indent=2))

        print("\n=== UPLOAD ===")
        with CSV_PATH.open("rb") as f:
            upload = client.post(
                f"{BASE}/sessions/{session_id}/upload",
                files=[("files", ("test_orders.csv", f, "text/csv"))],
            )
        print(f"Status: {upload.status_code}")
        print(json.dumps(upload.json(), indent=2))
        upload.raise_for_status()

        print("\n=== TABLES ===")
        tables = client.get(f"{BASE}/sessions/{session_id}/tables")
        print(json.dumps(tables.json(), indent=2))

        print("\n=== ASK (SQL) ===")
        ask = client.post(
            f"{BASE}/ask",
            json={
                "question": "How many orders are there in total?",
                "session_id": session_id,
            },
        )
        print(f"Status: {ask.status_code}")
        ask_data = ask.json()
        for key in ("route", "route_reason", "answer", "row_count", "error", "retry_count"):
            print(f"{key}: {ask_data.get(key)}")
        print(f"SQL: {ask_data.get('sql')}")
        print(f"Rows: {ask_data.get('rows', [])[:3]}")
        ask.raise_for_status()

        print("\n=== ASK (VIZ) ===")
        viz = client.post(
            f"{BASE}/ask",
            json={
                "question": "Create a bar chart of total revenue by state",
                "session_id": session_id,
            },
        )
        print(f"Status: {viz.status_code}")
        viz_data = viz.json()
        for key in ("route", "route_reason", "answer", "row_count", "chart_error"):
            print(f"{key}: {viz_data.get(key)}")
        print(f"Has chart_spec: {viz_data.get('chart_spec') is not None}")

    CSV_PATH.unlink(missing_ok=True)
    print("\nAll localhost tests passed.")


if __name__ == "__main__":
    main()
