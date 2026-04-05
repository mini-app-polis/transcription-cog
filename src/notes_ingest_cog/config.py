"""Configuration for notes-ingest-cog.

All settings are read from environment variables. Use .env.example as the
reference for required variables. Secrets are managed via Doppler → Railway.
"""

from __future__ import annotations

import os
from dataclasses import dataclass
from typing import Literal

LLMProvider = Literal["anthropic", "openai"]

_DEFAULT_MODELS: dict[str, str] = {
    "anthropic": "claude-sonnet-4-6",
    "openai": "gpt-4.1-mini",
}


def _require(name: str) -> str:
    v = os.getenv(name)
    if not v:
        raise RuntimeError(f"Missing required environment variable: {name}")
    return v


@dataclass(frozen=True)
class Config:
    # Google Drive
    notes_input_folder_id: str
    notes_processed_folder_id: str

    # LLM
    llm_provider: LLMProvider
    llm_model: str

    # Internal API
    kaiano_api_base_url: str
    kaiano_api_internal_key: str

    # Observability
    healthchecks_url: str
    sentry_dsn: str
    logging_level: str

    # Pipeline behaviour
    min_transcript_chars: int = 200

    @property
    def default_models(self) -> dict[str, str]:
        return _DEFAULT_MODELS


def load_config() -> Config:
    """Load and validate configuration from environment variables."""
    provider_raw = os.getenv("LLM_PROVIDER", "anthropic").lower().strip()
    if provider_raw not in ("anthropic", "openai"):
        raise RuntimeError(
            f"Unsupported LLM_PROVIDER: {provider_raw!r}. Must be 'anthropic' or 'openai'."
        )
    provider: LLMProvider = provider_raw  # type: ignore[assignment]
    model = os.getenv("LLM_MODEL", _DEFAULT_MODELS[provider])

    return Config(
        notes_input_folder_id=_require("NOTES_INPUT_FOLDER_ID"),
        notes_processed_folder_id=_require("NOTES_PROCESSED_FOLDER_ID"),
        llm_provider=provider,
        llm_model=model,
        kaiano_api_base_url=_require("KAIANO_API_BASE_URL"),
        kaiano_api_internal_key=_require("KAIANO_API_INTERNAL_KEY"),
        healthchecks_url=os.getenv("HEALTHCHECKS_URL", ""),
        sentry_dsn=os.getenv("SENTRY_DSN_NOTES_INGEST_COG", ""),
        logging_level=os.getenv("LOGGING_LEVEL", "INFO"),
        min_transcript_chars=int(os.getenv("MIN_TRANSCRIPT_CHARS", "200")),
    )
