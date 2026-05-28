"""FastAPI app for DataPilot.

v1 endpoints:
    GET  /health         — liveness probe
    GET  /schema         — current dataset schema (debug aid)
    POST /ask            — ask one question, get one answer
"""
from __future__ import annotations

import logging

from fastapi import FastAPI, HTTPException

from app.agent.graph import run_agent
from app.config import settings
from app.schemas import AskRequest, AskResponse
from app.tools.sql import get_sql_tool

logging.basicConfig(
    level=settings.log_level,
    format="%(asctime)s %(levelname)s %(name)s: %(message)s",
)
logger = logging.getLogger("datapilot")

app = FastAPI(
    title="DataPilot",
    version="0.1.0",
    description="Conversational data analysis for CSV files.",
)


@app.on_event("startup")
def _warm_up() -> None:
    # Eagerly load the dataset so the first request isn't slow.
    tool = get_sql_tool()
    logger.info("Dataset loaded from %s", tool.csv_path)
    logger.info("Table: %s", tool.table_name)


@app.get("/health")
def health() -> dict[str, str]:
    return {"status": "ok"}


@app.get("/schema")
def schema() -> dict[str, str]:
    return {"schema": get_sql_tool().schema_summary()}


@app.post("/ask", response_model=AskResponse)
def ask(req: AskRequest) -> AskResponse:
    try:
        final = run_agent(req.question)
    except Exception as e:  # noqa: BLE001
        logger.exception("Agent crashed")
        raise HTTPException(status_code=500, detail=f"Agent error: {e}") from e

    return AskResponse(
        question=req.question,
        answer=final.get("answer", ""),
        route=final.get("route"),
        route_reason=final.get("route_reason"),
        sql=final.get("sql"),
        columns=final.get("columns", []),
        rows=final.get("rows", []),
        row_count=final.get("row_count", 0),
        chart_spec=final.get("chart_spec") or None,
        error=final.get("error") or None,
    )
