"""Configuration for notes-ingest-cog."""

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
    notes_input_folder_id: str
    notes_processed_folder_id: str
    llm_provider: LLMProvider
    llm_model: str
    kaiano_api_base_url: str
    kaiano_api_internal_key: str
    healthchecks_url: str
    sentry_dsn: str
    logging_level: str
    min_transcript_chars: int = 200
    # Task retry delays — sourced from env so tests can set to 0 (TEST-013)
    task_retry_delay_short: int = 30
    task_retry_delay_long: int = 60

    @property
    def default_models(self) -> dict[str, str]:
        return _DEFAULT_MODELS


def load_config() -> Config:
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
        task_retry_delay_short=int(os.getenv("TASK_RETRY_DELAY_SHORT", "30")),
        task_retry_delay_long=int(os.getenv("TASK_RETRY_DELAY_LONG", "60")),
    )
