"""Single source of LLM clients.

All agent code goes through this module so swapping providers is one config
change, not a sweep through the codebase. Two helpers:

  - get_chat_llm()                 plain chat for SQL writing, etc.
  - get_structured_llm(SchemaCls)  same model with .with_structured_output()

Provider selection is driven by ``settings.llm_provider``:
  "groq"   — langchain_groq.ChatGroq (recommended; generous free tier)
  "gemini" — langchain_google_genai.ChatGoogleGenerativeAI
"""
from __future__ import annotations

import logging
from typing import Any, TypeVar

from pydantic import BaseModel

from app.config import settings

logger = logging.getLogger(__name__)

T = TypeVar("T", bound=BaseModel)


def _build_chat_llm() -> Any:
    """Construct the underlying chat LLM for the configured provider."""
    provider = settings.llm_provider.lower()

    if provider == "groq":
        if not settings.groq_api_key:
            raise RuntimeError(
                "LLM_PROVIDER=groq but GROQ_API_KEY is not set in .env. "
                "Get a key at https://console.groq.com."
            )
        from langchain_groq import ChatGroq

        logger.info("Using Groq model: %s", settings.groq_model)
        return ChatGroq(
            model=settings.groq_model,
            api_key=settings.groq_api_key,
            temperature=0.0,
        )

    if provider == "gemini":
        if not settings.gemini_api_key:
            raise RuntimeError(
                "LLM_PROVIDER=gemini but GEMINI_API_KEY is not set in .env. "
                "Get a key at https://aistudio.google.com."
            )
        from langchain_google_genai import ChatGoogleGenerativeAI

        logger.info("Using Gemini model: %s", settings.primary_model)
        return ChatGoogleGenerativeAI(
            model=settings.primary_model,
            google_api_key=settings.gemini_api_key,
            temperature=0.0,
        )

    raise RuntimeError(
        f"Unknown LLM_PROVIDER '{settings.llm_provider}'. "
        "Expected 'groq' or 'gemini'."
    )


# --- Cached singletons ------------------------------------------------------

_chat_llm: Any = None
_structured_llms: dict[type[BaseModel], Any] = {}


def get_chat_llm() -> Any:
    """Plain chat LLM (free-form text output)."""
    global _chat_llm
    if _chat_llm is None:
        _chat_llm = _build_chat_llm()
    return _chat_llm


def get_structured_llm(schema: type[T]) -> Any:
    """Chat LLM that returns Pydantic model instances of ``schema``.

    Cached per-schema. Uses langchain's with_structured_output so both
    Gemini and Groq go through the same interface.
    """
    if schema not in _structured_llms:
        base = get_chat_llm()
        _structured_llms[schema] = base.with_structured_output(schema)
    return _structured_llms[schema]


def reset_cache() -> None:
    """Drop cached clients. Useful in tests or after a config change."""
    global _chat_llm
    _chat_llm = None
    _structured_llms.clear()
