"""File ingestion: validate, convert CSV/Excel → Parquet, register with session.

The pipeline:
  1. Validate: file extension, size cap, parseable content
  2. Read into a pandas DataFrame (temporarily in memory — small since cap is 10MB)
  3. Write as Parquet (lossless compression, typically 2-10x smaller)
  4. Return metadata (table name, column schema, row count, Parquet path)

Why Parquet:
  - Smaller on disk → saves Supabase Storage / local space
  - DuckDB queries Parquet directly from disk via read_parquet()
  - No RAM overhead at query time (unlike loading a full CSV into memory)
  - Lossless — identical data, just a different container
"""
from __future__ import annotations

import logging
import re
from dataclasses import dataclass
from pathlib import Path
from typing import BinaryIO

import pandas as pd

logger = logging.getLogger(__name__)

# --- Constants ---------------------------------------------------------------

ALLOWED_EXTENSIONS = {".csv", ".xlsx", ".xls"}
MAX_FILE_SIZE_BYTES = 10 * 1024 * 1024  # 10 MB
MAX_FILES_PER_BATCH = 5

# Table name sanitization: only allow [a-z0-9_], replace everything else.
_TABLE_NAME_RE = re.compile(r"[^a-z0-9_]")


# --- Exceptions --------------------------------------------------------------


class IngestionError(Exception):
    """Raised when a file fails validation or conversion."""
    pass


# --- Data classes ------------------------------------------------------------


@dataclass
class IngestedFile:
    """Result of successfully ingesting one file."""
    original_filename: str
    table_name: str
    parquet_path: Path
    columns: list[str]
    dtypes: dict[str, str]  # column → Parquet/pandas dtype as string
    row_count: int
    size_bytes: int  # Parquet file size on disk


# --- Public API --------------------------------------------------------------


def validate_and_convert(
    filename: str,
    file_data: BinaryIO,
    output_dir: Path,
    existing_table_names: list[str] | None = None,
) -> IngestedFile:
    """Validate an uploaded file and convert it to Parquet.

    Parameters
    ----------
    filename : str
        Original filename from the upload (e.g., "sales_2024.csv").
    file_data : BinaryIO
        File-like object with the raw bytes.
    output_dir : Path
        Directory where the Parquet file will be written.
    existing_table_names : list[str] | None
        Table names already in this session — used to detect collisions.

    Returns
    -------
    IngestedFile with metadata about the converted file.

    Raises
    ------
    IngestionError on any validation or conversion failure.
    """
    # --- Extension check ---
    ext = Path(filename).suffix.lower()
    if ext not in ALLOWED_EXTENSIONS:
        raise IngestionError(
            f"Unsupported file type '{ext}'. Allowed: {sorted(ALLOWED_EXTENSIONS)}"
        )

    # --- Size check (read all bytes — we need them for pandas anyway) ---
    raw_bytes = file_data.read()
    if len(raw_bytes) > MAX_FILE_SIZE_BYTES:
        size_mb = len(raw_bytes) / (1024 * 1024)
        cap_mb = MAX_FILE_SIZE_BYTES / (1024 * 1024)
        raise IngestionError(
            f"File is {size_mb:.1f} MB, exceeds the {cap_mb:.0f} MB limit."
        )

    if len(raw_bytes) == 0:
        raise IngestionError("File is empty.")

    # --- Parse into DataFrame ---
    import io

    try:
        if ext == ".csv":
            df = pd.read_csv(io.BytesIO(raw_bytes))
        elif ext in (".xlsx", ".xls"):
            df = pd.read_excel(io.BytesIO(raw_bytes), engine="openpyxl")
        else:
            raise IngestionError(f"No parser for extension '{ext}'.")
    except IngestionError:
        raise
    except Exception as e:
        raise IngestionError(f"Failed to parse file: {type(e).__name__}: {e}") from e

    if df.empty or len(df.columns) == 0:
        raise IngestionError("File parsed but contains no data or no columns.")

    if len(df) == 0:
        raise IngestionError("File has column headers but zero data rows.")

    # --- Derive table name ---
    table_name = _derive_table_name(filename, existing_table_names or [])

    # --- Write Parquet ---
    output_dir.mkdir(parents=True, exist_ok=True)
    parquet_path = output_dir / f"{table_name}.parquet"

    try:
        df.to_parquet(parquet_path, index=False, engine="pyarrow")
    except Exception as e:
        raise IngestionError(f"Failed to write Parquet: {type(e).__name__}: {e}") from e

    parquet_size = parquet_path.stat().st_size
    logger.info(
        "Ingested %s → %s (%d rows, %d cols, %.1f KB Parquet)",
        filename, table_name, len(df), len(df.columns), parquet_size / 1024,
    )

    return IngestedFile(
        original_filename=filename,
        table_name=table_name,
        parquet_path=parquet_path,
        columns=list(df.columns),
        dtypes={col: str(dtype) for col, dtype in df.dtypes.items()},
        row_count=len(df),
        size_bytes=parquet_size,
    )


# --- Internals ---------------------------------------------------------------


def _derive_table_name(filename: str, existing: list[str]) -> str:
    """Turn a filename into a valid, unique SQL table name.

    Examples:
        "Sales Data 2024.csv"  → "sales_data_2024"
        "my-file (1).xlsx"     → "my_file_1"
        collision with existing → appends _2, _3, etc.
    """
    stem = Path(filename).stem.lower().strip()
    # Replace non-alphanumeric with underscore
    name = _TABLE_NAME_RE.sub("_", stem)
    # Collapse multiple underscores
    name = re.sub(r"_+", "_", name).strip("_")
    # Ensure it starts with a letter (SQL requirement)
    if not name or not name[0].isalpha():
        name = "t_" + name
    # Handle collisions
    if name not in existing:
        return name
    for i in range(2, 100):
        candidate = f"{name}_{i}"
        if candidate not in existing:
            return candidate
    raise IngestionError(f"Could not find unique table name for '{filename}'.")
