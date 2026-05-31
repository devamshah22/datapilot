"""Result validator.

Rule-based, no LLM call. Catches the common failure modes that an LLM
naturally produces:

  1. Wrong column name → SQL execution error (already in state.error)
  2. Over-restrictive filter → zero rows on a question that implies rows
  3. Type mismatch → execution error or zero rows

LLM-based plausibility checking ("does this number look right?") is
deliberately NOT in v1. It's expensive per question and easy to
over-correct on legitimate empty results.
"""
from __future__ import annotations

import re
from typing import Any

from app.agent.state import AgentState


# Phrases that strongly imply the user expects at least one row back.
# If the SQL returns zero rows on a question with one of these, the
# query is probably under-restricted or filters too narrowly.
_NON_EMPTY_HINTS = re.compile(
    r"\b(top|highest|lowest|most|least|best|worst|biggest|smallest|"
    r"average|mean|median|sum|count|how many|which|who|what is the)\b",
    re.IGNORECASE,
)


def validator_node(state: AgentState) -> dict[str, Any]:
    """Decide if the latest SQL result is plausible.

    Sets state["validation_failure"] with a short message when the result
    looks wrong. Empty string (or absent) means the result is acceptable.
    """
    # SQL execution already failed — no need to validate further.
    if state.get("error"):
        return {"validation_failure": ""}

    rows = state.get("rows", [])
    row_count = state.get("row_count", 0)
    question = state.get("question", "")

    if row_count == 0 and _NON_EMPTY_HINTS.search(question):
        return {
            "validation_failure": (
                "Query returned zero rows but the question implies at least "
                "one row should match. The WHERE clause may be too restrictive "
                "or use values not present in the data."
            )
        }

    # All-null in the only data column is also suspicious.
    cols = state.get("columns", [])
    if rows and len(cols) == 1 and all(r[cols[0]] is None for r in rows):
        return {
            "validation_failure": (
                "Query result column is entirely NULL. The aggregation may "
                "be referencing a column that has no non-null values matching "
                "the filter."
            )
        }

    return {"validation_failure": ""}
