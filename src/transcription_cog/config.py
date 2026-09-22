"""Configuration for transcription-cog."""

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
    """TODO: describe this class."""

    notes_input_folder_id: str
    notes_processed_folder_id: str
    llm_provider: LLMProvider
    llm_model: str
    kaiano_api_base_url: str
    logging_level: str
    min_transcript_chars: int = 200

    @property
    def default_models(self) -> dict[str, str]:
        """TODO: describe this function."""
        return _DEFAULT_MODELS


def load_config() -> Config:
    """TODO: describe this function."""
    provider_raw = os.getenv("LLM_PROVIDER", "anthropic").lower().strip()
    if provider_raw not in ("anthropic", "openai"):
        raise RuntimeError(
            f"Unsupported LLM_PROVIDER: {provider_raw!r}. Must be 'anthropic' or 'openai'."
        )
    provider: LLMProvider = provider_raw  # type: ignore[assignment]
    model = os.getenv("LLM_MODEL", _DEFAULT_MODELS[provider])

    # Auth env vars (TRANSCRIPTION_COG_API_KEY) are read directly by
    # KaianoApiClient.from_env() in api_client.py — validated there rather
    # than duplicated into Config.
    return Config(
        notes_input_folder_id=_require("NOTES_INPUT_FOLDER_ID"),
        notes_processed_folder_id=_require("NOTES_PROCESSED_FOLDER_ID"),
        llm_provider=provider,
        llm_model=model,
        kaiano_api_base_url=_require("KAIANO_API_BASE_URL"),
        logging_level=os.getenv("LOGGING_LEVEL", "INFO"),
        min_transcript_chars=int(os.getenv("MIN_TRANSCRIPT_CHARS", "200")),
    )
