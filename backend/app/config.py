"""Application configuration loaded from environment variables.

All settings live here. Modules import `settings` and never read os.environ directly.
"""
from __future__ import annotations

from pathlib import Path

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict

# Project root = two levels up from this file (backend/app/config.py -> repo root)
ROOT_DIR = Path(__file__).resolve().parents[2]


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=ROOT_DIR / ".env",
        env_file_encoding="utf-8",
        extra="ignore",
    )

    # --- LLM ---
    gemini_api_key: str = Field(..., alias="GEMINI_API_KEY")
    groq_api_key: str | None = Field(default=None, alias="GROQ_API_KEY")
    primary_model: str = Field(default="gemini-2.5-flash", alias="PRIMARY_MODEL")
    validator_model: str = Field(default="gemini-2.5-flash", alias="VALIDATOR_MODEL")

    # --- Data ---
    dataset_path: Path = Field(
        default=ROOT_DIR / "data" / "olist_v1_flat.csv",
        alias="DATASET_PATH",
    )
    dataset_table_name: str = Field(default="orders", alias="DATASET_TABLE_NAME")

    # --- Agent behavior ---
    max_agent_retries: int = Field(default=3, alias="MAX_AGENT_RETRIES")
    log_level: str = Field(default="INFO", alias="LOG_LEVEL")


settings = Settings()  # singleton; raises at import time if required env is missing
