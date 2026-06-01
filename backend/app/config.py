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
    # Which provider to use. "groq" is recommended for the free tier
    # (much higher daily quota than Gemini free tier).
    llm_provider: str = Field(default="groq", alias="LLM_PROVIDER")

    gemini_api_key: str | None = Field(default=None, alias="GEMINI_API_KEY")
    groq_api_key: str | None = Field(default=None, alias="GROQ_API_KEY")

    # Per-provider model names
    primary_model: str = Field(default="gemini-2.0-flash", alias="PRIMARY_MODEL")
    validator_model: str = Field(default="gemini-2.0-flash", alias="VALIDATOR_MODEL")
    groq_model: str = Field(default="llama-3.3-70b-versatile", alias="GROQ_MODEL")

    # --- Data ---
    dataset_path: Path = Field(
        default=ROOT_DIR / "data" / "olist_v1_flat.csv",
        alias="DATASET_PATH",
    )
    dataset_table_name: str = Field(default="orders", alias="DATASET_TABLE_NAME")

    # --- Agent behavior ---
    max_agent_retries: int = Field(default=3, alias="MAX_AGENT_RETRIES")
    log_level: str = Field(default="INFO", alias="LOG_LEVEL")

    # --- Session memory ---
    # "memory" (process-local, lost on restart) or "supabase" (persistent Postgres).
    session_backend: str = Field(default="memory", alias="SESSION_BACKEND")
    session_ttl_seconds: int = Field(default=0, alias="SESSION_TTL_SECONDS")
    supabase_url: str | None = Field(default=None, alias="SUPABASE_URL")
    supabase_key: str | None = Field(default=None, alias="SUPABASE_KEY")

    # --- Safety limits ---
    sql_timeout_seconds: float = Field(default=10.0, alias="SQL_TIMEOUT_SECONDS")
    rate_limit_ask: str = Field(default="10/minute", alias="RATE_LIMIT_ASK")
    rate_limit_default: str = Field(default="60/minute", alias="RATE_LIMIT_DEFAULT")
    cors_allowed_origins: str = Field(default="*", alias="CORS_ALLOWED_ORIGINS")

    @property
    def cors_origins_list(self) -> list[str]:
        """Parse CORS_ALLOWED_ORIGINS into a list (comma-separated, or single '*')."""
        raw = self.cors_allowed_origins.strip()
        if raw == "*":
            return ["*"]
        return [o.strip() for o in raw.split(",") if o.strip()]


settings = Settings()  # singleton; raises at import time if required env is missing
