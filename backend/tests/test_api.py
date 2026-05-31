"""HTTP-level tests for the FastAPI app.

These exercise routing, request/response shape, and middleware. They
intentionally avoid mocking the LLM — Groq is fast and the cost per test
run is negligible. If you're rate-limited locally, mark these with
``pytest -k 'not test_api'`` to skip.
"""
from __future__ import annotations

import pytest
from fastapi.testclient import TestClient


@pytest.fixture(scope="module")
def client() -> TestClient:
    from app.main import app
    return TestClient(app)


def test_health_returns_ok(client: TestClient) -> None:
    r = client.get("/health")
    assert r.status_code == 200
    assert r.json() == {"status": "ok"}


def test_schema_returns_table_summary(client: TestClient) -> None:
    r = client.get("/schema")
    assert r.status_code == 200
    body = r.json()
    assert "schema" in body
    assert "Table: orders" in body["schema"]


def test_ask_accepts_json_body_and_returns_full_response(client: TestClient) -> None:
    """Regression: with slowapi's @limiter.limit decorator wrapping /ask,
    FastAPI used to mis-detect the AskRequest model as a query parameter
    and return 422. Body(...) annotation in main.py fixes it. This test
    locks the contract in so the bug can't sneak back."""
    r = client.post("/ask", json={"question": "How many orders are there?"})
    assert r.status_code == 200, r.text
    body = r.json()
    # Required envelope fields
    for key in ("question", "answer", "route", "row_count"):
        assert key in body
    # Question echoed back
    assert body["question"] == "How many orders are there?"
    # Should resolve to the SQL route for a simple count question
    assert body["route"] == "sql"


def test_ask_rejects_missing_question(client: TestClient) -> None:
    r = client.post("/ask", json={})
    assert r.status_code == 422  # Pydantic validation error
    assert "question" in r.text


def test_ask_rejects_overly_long_question(client: TestClient) -> None:
    huge = "x" * 3000
    r = client.post("/ask", json={"question": huge})
    assert r.status_code == 422
