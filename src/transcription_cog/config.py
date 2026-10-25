"""Configuration for transcription-cog."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Literal

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict

LLMProvider = Literal["anthropic", "openai"]

_DEFAULT_MODELS: dict[str, str] = {
    "anthropic": "claude-sonnet-4-6",
    "openai": "gpt-4.1-mini",
}


class WcsSettings(BaseSettings):
    """The environment the WCS pipeline reads, one field per name.

    Read afresh by every :func:`load_config` call, so a value refreshed
    into the environment applies to the next run. The required folders
    default to empty so a missing one is reported by name there, rather
    than as a validation error.
    """

    model_config = SettingsConfigDict(
        # The process environment only: Doppler supplies it (`doppler run`
        # locally, SSM on Lambda), and no .env file is read.
        extra="ignore",
        case_sensitive=False,
    )

    notes_input_folder_id: str = Field(
        default="",
        description="Drive folder new transcripts arrive in. Required.",
    )
    notes_processed_folder_id: str = Field(
        default="",
        description="Drive folder processed transcripts are archived to. Required.",
    )
    llm_provider: str = Field(
        default="anthropic",
        description="The extraction's provider: anthropic or openai.",
    )
    llm_model: str | None = Field(
        default=None,
        description="The extraction's model. Unset: the provider's default.",
    )
    logging_level: str = Field(
        default="INFO",
        description="Log level: DEBUG, INFO, WARN or ERROR.",
    )
    min_transcript_chars: int = Field(
        default=200,
        description="Shortest transcript processed, in characters.",
    )
    min_note_chars: int = Field(
        default=20,
        description="Shortest authored note or correction processed, in characters.",
    )


def _require(value: str, name: str) -> str:
    if not value:
        raise RuntimeError(f"Missing required environment variable: {name}")
    return value


@dataclass(frozen=True)
class Config:
    """TODO: describe this class."""

    notes_input_folder_id: str
    notes_processed_folder_id: str
    llm_provider: LLMProvider
    llm_model: str
    logging_level: str
    min_transcript_chars: int = 200
    #: The length floor for an authored note. Far below the transcript floor:
    #: a correction can be one sentence, and a note is typed on purpose, so
    #: the garbled-or-empty-recording case the transcript floor guards
    #: against does not apply. It still refuses an empty file.
    min_note_chars: int = 20

    @property
    def default_models(self) -> dict[str, str]:
        """TODO: describe this function."""
        return _DEFAULT_MODELS


def load_config() -> Config:
    """The WCS pipeline's configuration, read from the environment now.

    Raises RuntimeError naming a required folder that is unset, or an
    unsupported LLM_PROVIDER.
    """
    env = WcsSettings()
    provider_raw = env.llm_provider.lower().strip()
    if provider_raw not in ("anthropic", "openai"):
        raise RuntimeError(
            f"Unsupported LLM_PROVIDER: {provider_raw!r}. Must be 'anthropic' or 'openai'."
        )
    provider: LLMProvider = provider_raw  # type: ignore[assignment]
    model = env.llm_model if env.llm_model is not None else _DEFAULT_MODELS[provider]

    # The API's base URL and this cog's key (TRANSCRIPTION_COG_API_KEY)
    # are read directly by KaianoApiClient.from_env() in flow.py, which
    # picks the name for this environment — validated there rather than
    # duplicated into Config.
    return Config(
        notes_input_folder_id=_require(
            env.notes_input_folder_id, "NOTES_INPUT_FOLDER_ID"
        ),
        notes_processed_folder_id=_require(
            env.notes_processed_folder_id, "NOTES_PROCESSED_FOLDER_ID"
        ),
        llm_provider=provider,
        llm_model=model,
        logging_level=env.logging_level,
        min_transcript_chars=env.min_transcript_chars,
        min_note_chars=env.min_note_chars,
    )
