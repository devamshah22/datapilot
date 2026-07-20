"""File storage via Supabase Storage.

Uploaded Parquet files are persisted in a Supabase Storage bucket so they
survive server restarts. On session load, if the local file is missing,
it's downloaded from Supabase Storage and the DuckDB connection is rebuilt.

Bucket structure:
  uploads/{session_id}/{table_name}.parquet
"""
from __future__ import annotations

import logging
from pathlib import Path

from app.config import settings

logger = logging.getLogger(__name__)

BUCKET_NAME = "uploads"


def _get_storage():
    """Get the Supabase storage client."""
    from supabase import create_client
    client = create_client(settings.supabase_url, settings.supabase_key)
    return client.storage


def ensure_bucket_exists() -> None:
    """Create the uploads bucket if it doesn't exist."""
    storage = _get_storage()
    try:
        storage.get_bucket(BUCKET_NAME)
    except Exception:
        try:
            storage.create_bucket(BUCKET_NAME, options={"public": False})
            logger.info("Created Supabase storage bucket: %s", BUCKET_NAME)
        except Exception as e:
            # Bucket might already exist (race condition or permission)
            logger.debug("Bucket creation skipped: %s", e)


def upload_parquet(session_id: str, table_name: str, local_path: Path) -> str:
    """Upload a Parquet file to Supabase Storage.

    Returns the storage path (e.g., 'uploads/abc123/sales.parquet').
    """
    storage = _get_storage()
    remote_path = f"{session_id}/{table_name}.parquet"

    with open(local_path, "rb") as f:
        storage.from_(BUCKET_NAME).upload(
            remote_path,
            f,
            file_options={"content-type": "application/octet-stream", "upsert": "true"},
        )

    logger.info("Uploaded %s to storage: %s/%s", local_path.name, BUCKET_NAME, remote_path)
    return remote_path


def download_parquet(session_id: str, table_name: str, local_dir: Path) -> Path:
    """Download a Parquet file from Supabase Storage to a local path.

    Returns the local path where the file was saved.
    """
    storage = _get_storage()
    remote_path = f"{session_id}/{table_name}.parquet"
    local_path = local_dir / f"{table_name}.parquet"

    local_dir.mkdir(parents=True, exist_ok=True)

    data = storage.from_(BUCKET_NAME).download(remote_path)
    local_path.write_bytes(data)

    logger.info("Downloaded %s/%s to %s", BUCKET_NAME, remote_path, local_path)
    return local_path


def file_exists_in_storage(session_id: str, table_name: str) -> bool:
    """Check if a Parquet file exists in Supabase Storage."""
    storage = _get_storage()
    remote_path = f"{session_id}/{table_name}.parquet"
    try:
        # List files in the session folder and check
        files = storage.from_(BUCKET_NAME).list(session_id)
        return any(f["name"] == f"{table_name}.parquet" for f in files)
    except Exception:
        return False


def delete_session_files(session_id: str) -> None:
    """Delete all files for a session from Supabase Storage."""
    storage = _get_storage()
    try:
        files = storage.from_(BUCKET_NAME).list(session_id)
        if files:
            paths = [f"{session_id}/{f['name']}" for f in files]
            storage.from_(BUCKET_NAME).remove(paths)
            logger.info("Deleted %d files for session %s", len(paths), session_id)
    except Exception as e:
        logger.warning("Failed to delete storage files for session %s: %s", session_id, e)
