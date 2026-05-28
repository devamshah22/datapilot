"""Pytest fixtures shared across backend tests."""
from __future__ import annotations

import sys
from pathlib import Path

import pytest

# Make `import app` work when tests are run from anywhere.
ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from app.tools.sql import SQLTool  # noqa: E402


@pytest.fixture(scope="session")
def sql_tool() -> SQLTool:
    """One SQLTool per test session; loading the CSV is the slow part."""
    return SQLTool()
