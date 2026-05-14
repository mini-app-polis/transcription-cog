"""Configuration for voicenotes-cog.

All runtime config flows through pydantic-settings. Doppler injects env vars;
this module just reads them. Defaults are deliberately conservative.

Usage:
    from notes_ingest_cog.voicenotes.config import settings
    settings.todoist_inbox_project_id
"""

from __future__ import annotations

from functools import lru_cache

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    """Runtime configuration. All fields are required unless they have a default."""

    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        # Avoid silently picking up unrelated env vars from the host.
        extra="ignore",
        case_sensitive=False,
    )

    # --- LLM providers ---
    openai_api_key: str = Field(..., description="OpenAI API key for Whisper.")
    anthropic_api_key: str = Field(..., description="Anthropic API key for Claude.")
    claude_model: str = Field(
        default="claude-sonnet-4-6",
        description="Claude model used for the extraction call. See docs/PROMPT.md.",
    )
    whisper_model: str = Field(
        default="whisper-1",
        description=(
            "OpenAI Whisper model. Currently only whisper-1 is publicly available."
        ),
    )

    # --- Todoist ---
    # Todoist uses a long-lived personal API token (no OAuth). The
    # token is minted from the Todoist app at
    # Settings → Integrations → Developer and remains valid until
    # the operator manually revokes it. On 401/403 the runtime
    # client raises TodoistAuthError; the fix is to mint a new
    # token and rotate TODOIST_API_TOKEN in Doppler.
    todoist_api_token: str = Field(
        ...,
        description=(
            "Long-lived Todoist personal API token. Bootstrap via "
            "scripts/setup_todoist.py."
        ),
    )
    todoist_inbox_project_id: str = Field(
        ...,
        description="Todoist project ID where voice notes are posted.",
    )

    # --- Google Drive ---
    google_drive_voice_inbox_folder_id: str = Field(
        ...,
        description="Drive folder ID for the voice-inbox/ root.",
    )
    # Drive auth itself is handled by common-python-utils, which sources its
    # own credentials. We don't duplicate that config here.

    # --- Observability ---
    # No Healthchecks.io ping here: watcher-cog already has its own
    # healthcheck for the trigger arm, and Prefect Cloud's "flow run
    # failed" / "missed scheduled run" alerts cover the gap on this
    # side without a separate dead-man's switch.
    sentry_dsn_voicenotes: str | None = Field(
        default=None,
        description=(
            "Sentry DSN for THIS cog. Suffixed with the cog name "
            "so a shared Doppler project across the ecosystem can "
            "hold a distinct DSN per cog without collision. "
            "Optional in dev; required in prod."
        ),
    )

    # --- API integration (per ecosystem-standards CD-012) ---
    # ``mini_app_polis.api.KaianoApiClient`` reads these env vars
    # itself; we surface them through Settings so all secrets flow
    # through pydantic-settings + Doppler. The client exchanges the
    # machine secret for a short-lived Clerk M2M opaque token,
    # caches it until ~60s before expiry, and refreshes
    # automatically.
    kaiano_api_base_url: str = Field(
        default="https://api.kaianolevine.com",
        description=(
            "Base URL for api-kaianolevine-com (POST /v1/evaluations endpoint)."
        ),
    )
    kaiano_api_clerk_machine_secret: str | None = Field(
        default=None,
        description=(
            "Clerk machine-secret-key for the miniappolis-cogs machine. "
            "Used by KaianoApiClient to mint short-lived Clerk M2M "
            "tokens. Required in production."
        ),
    )

    # --- Behavior ---
    archive_retention_days: int = Field(
        default=14,
        ge=1,
        description=(
            "Days to keep audio in processed/ before cleanup deletes it. "
            "14 days is more than enough for the typical 'I want to "
            "re-listen to that note from last week' use case; older audio "
            "is rarely consulted and the Todoist task description is the "
            "durable record."
        ),
    )

    # --- Operational delays (TEST-013) ---
    # Sourced from settings so tests can override to zero without
    # touching source. Production defaults below mirror the original
    # hardcoded literals.
    task_retries: int = Field(
        default=3,
        ge=0,
        description="Default Prefect task retry count for ingest tasks.",
    )
    task_retry_delays_seconds: list[int] = Field(
        default=[5, 15, 30],
        description=(
            "Per-attempt retry delay (seconds) for ingest tasks. "
            "Length should match task_retries."
        ),
    )
    extract_task_retries: int = Field(
        default=2,
        ge=0,
        description="Retry count for the Claude extraction task.",
    )
    extract_task_retry_delays_seconds: list[int] = Field(
        default=[5, 15],
        description="Retry delays for the Claude extraction task.",
    )

    # HTTP client default timeout for outbound API calls (Todoist,
    # Drive, etc.). Surfaced in settings so tests can override to a
    # tiny value without monkeypatching, per TEST-013.
    http_timeout_seconds: float = Field(
        default=10.0,
        ge=0.0,
        description="Default HTTP timeout for outbound API calls.",
    )

    environment: str = Field(default="production")


@lru_cache
def get_settings() -> Settings:
    """Cached settings accessor.

    Why a function and not a module-level Settings()? So that tests can
    override env vars and clear the cache between cases:
        from notes_ingest_cog.voicenotes.config import get_settings
        get_settings.cache_clear()
    """
    return Settings()  # type: ignore[call-arg]


# Convenience alias for production code.
settings = get_settings()
