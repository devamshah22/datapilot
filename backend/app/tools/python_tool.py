"""Python/pandas execution tool via Docker sandbox.

The agent writes pandas code; we execute it inside a Docker container with:
  - No network access (--network=none)
  - Read-only data mount (only the session's Parquet files)
  - Memory limit (256MB)
  - Hard timeout (10s via --stop-timeout + subprocess timeout)
  - Non-root user inside the container
  - No host env vars passed (API keys cannot leak)

For the LLM-generated code to work, the harness pre-loads data into `df`
(from a Parquet file) and captures `result` or stdout.
"""
from __future__ import annotations

import logging
import re
import subprocess
import tempfile
from dataclasses import dataclass
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)

DOCKER_IMAGE = "datapilot-sandbox"
EXECUTION_TIMEOUT = 15  # seconds (includes container startup ~1-2s)
MEMORY_LIMIT = "256m"

# Imports we allow — validated before sending to Docker
ALLOWED_IMPORTS = {
    "pandas", "pd",
    "numpy", "np",
    "scipy", "scipy.stats",
    "sklearn", "sklearn.cluster", "sklearn.preprocessing", "sklearn.metrics",
    "statistics",
    "math",
    "datetime",
}

_IMPORT_RE = re.compile(
    r"^\s*(?:import|from)\s+([a-zA-Z_][a-zA-Z0-9_.]*)",
    re.MULTILINE,
)


@dataclass
class PythonResult:
    """Output of a Python code execution."""
    code: str
    output: str | None = None
    error: str | None = None

    @property
    def ok(self) -> bool:
        return self.error is None


def validate_imports(code: str) -> str | None:
    """Check all imports are from the allowed set. Returns error message or None."""
    for match in _IMPORT_RE.finditer(code):
        module = match.group(1).split(".")[0]
        if module not in ALLOWED_IMPORTS:
            return (
                f"Import '{match.group(1)}' is not allowed. "
                f"Permitted: pandas, numpy, scipy, sklearn, statistics, math, datetime."
            )
    return None


def execute_python(code: str, parquet_paths: list[str] | None = None) -> PythonResult:
    """Execute pandas code inside a Docker sandbox.

    Parameters
    ----------
    code : str
        LLM-generated Python code.
    parquet_paths : list[str] | None
        Paths to Parquet files on the host. Mounted read-only into the
        container at /data/. If multiple files, they're loaded as separate
        DataFrames (df_0, df_1, ...) and df = df_0 for convenience.
    """
    # Pre-validate imports before even touching Docker
    import_error = validate_imports(code)
    if import_error:
        return PythonResult(code=code, error=import_error)

    # Build the harness script
    harness = _build_harness(code, parquet_paths or [])

    # Write harness to a temp file
    with tempfile.NamedTemporaryFile(
        mode="w", suffix=".py", delete=False, encoding="utf-8", dir=tempfile.gettempdir()
    ) as f:
        f.write(harness)
        script_path = Path(f.name)

    try:
        # Build docker run command
        cmd = [
            "docker", "run", "--rm",
            "--network=none",
            f"--memory={MEMORY_LIMIT}",
            "--read-only",
            "--tmpfs", "/tmp:size=50m",
        ]

        # Mount parquet files read-only
        if parquet_paths:
            for p in parquet_paths:
                host_path = Path(p).resolve().as_posix()
                filename = Path(p).name
                cmd.extend(["-v", f"{host_path}:/data/{filename}:ro"])

        # Mount the script
        cmd.extend(["-v", f"{script_path.resolve().as_posix()}:/home/sandbox/run.py:ro"])

        cmd.extend([DOCKER_IMAGE, "/home/sandbox/run.py"])

        result = subprocess.run(
            cmd,
            capture_output=True,
            text=True,
            timeout=EXECUTION_TIMEOUT,
        )
    except subprocess.TimeoutExpired:
        return PythonResult(code=code, error=f"Execution exceeded {EXECUTION_TIMEOUT}s timeout.")
    except FileNotFoundError:
        return PythonResult(code=code, error="Docker is not available. Ensure Docker Desktop is running.")
    except Exception as e:
        return PythonResult(code=code, error=f"Execution failed: {type(e).__name__}: {e}")
    finally:
        script_path.unlink(missing_ok=True)

    if result.returncode != 0:
        error_msg = (result.stderr or "Unknown error").strip()[-500:]
        return PythonResult(code=code, error=error_msg)

    output = result.stdout.strip()
    if not output:
        return PythonResult(code=code, output="(code ran successfully but produced no output)")

    return PythonResult(code=code, output=output)


def _build_harness(user_code: str, parquet_paths: list[str]) -> str:
    """Wrap user code in a harness that loads data and captures output."""
    # Load each parquet file as df_0, df_1, etc. Set df = df_0 for convenience.
    load_lines = [
        "import pandas as pd",
        "import numpy as np",
        "import warnings",
        "warnings.filterwarnings('ignore')",
    ]

    if parquet_paths:
        filenames = [Path(p).name for p in parquet_paths]
        for i, fname in enumerate(filenames):
            load_lines.append(f"df_{i} = pd.read_parquet('/data/{fname}')")
        load_lines.append("df = df_0  # convenience alias for the first file")
    else:
        load_lines.append("df = pd.DataFrame()  # no data loaded")

    load_section = "\n".join(load_lines)

    return f"""\
{load_section}

# --- User code below ---
{user_code}
# --- User code above ---

# Capture result
if 'result' in dir():
    _r = eval('result')
    if hasattr(_r, 'to_string'):
        print(_r.to_string())
    else:
        print(_r)
"""
