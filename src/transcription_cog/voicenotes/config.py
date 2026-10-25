"""Configuration for voicenotes sub-pipeline.

All runtime config flows through pydantic-settings. Doppler injects env vars;
this module just reads them. Defaults are deliberately conservative.

Validation-deferral note
------------------------
The voicenotes-specific required fields (``openai_api_key``,
``anthropic_api_key``, ``google_drive_voice_inbox_folder_id``)
are declared with empty-string
defaults rather than ``Field(...)``-required. This is deliberate:
transcription-cog ships as a single deployment hosting two
unrelated pipelines, and Doppler may have voicenotes secrets configured
only after the merge deploy. Hard-requiring them at module import would
crash the entire deployment — including ``wcs-transcripts`` mode, which
has nothing to do with voicenotes — every time someone forgot to
populate one of these.

Instead, callers (the voicenotes flows) MUST call
``require_voicenotes_settings()`` at flow entry. That helper raises a
clear ``RuntimeError`` enumerating any missing fields, so failure is
loud and localised to the voicenotes mode rather than masking as a
generic pydantic ValidationError at boot.

Usage:
    from transcription_cog.voicenotes.config import settings
    settings.google_drive_voice_inbox_folder_id
"""

from __future__ import annotations

from functools import lru_cache

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    """Runtime configuration.

    Voicenotes-required fields default to empty strings — see module
    docstring. Call ``require_voicenotes_settings()`` at the entry of
    any voicenotes flow before consuming these values.
    """

    model_config = SettingsConfigDict(
        # The process environment only: Doppler supplies it (`doppler run`
        # locally, SSM on Lambda), and no .env file is read.
        # Avoid silently picking up unrelated env vars from the host.
        extra="ignore",
        case_sensitive=False,
    )

    # --- LLM providers ---
    # Empty-string defaults: see module docstring (validation deferred to
    # ``require_voicenotes_settings()`` so wcs-transcripts mode can boot
    # without voicenotes secrets in Doppler).
    openai_api_key: str = Field(
        default="",
        description=(
            "OpenAI API key for Whisper. Required for voicenotes mode; "
            "boot is allowed to succeed without it so wcs-transcripts "
            "mode can serve."
        ),
    )
    anthropic_api_key: str = Field(
        default="",
        description=(
            "Anthropic API key for Claude. Required for voicenotes mode; "
            "the parent WCS pipeline also reads ANTHROPIC_API_KEY directly "
            "for its own LLM config."
        ),
    )
    claude_model: str = Field(
        default="claude-sonnet-4-6",
        description=(
            "Claude model used for the extraction call. The prompt is "
            "voicenotes/prompts/extract.md."
        ),
    )
    whisper_model: str = Field(
        default="whisper-1",
        description=(
            "OpenAI Whisper model. Currently only whisper-1 is publicly available."
        ),
    )

    # --- Google Drive ---
    google_drive_voice_inbox_folder_id: str = Field(
        default="",
        description=(
            "Drive folder ID for the voice-inbox/ root. Required for voicenotes mode."
        ),
    )
    # Drive auth itself is handled by common-python-utils, which sources its
    # own credentials. We don't duplicate that config here.

    # --- Observability ---
    # No Healthchecks.io ping here: watcher-cog already has its own
    # healthcheck for the trigger arm, and on this side a job that fails
    # every retry lands in the dead-letter queue, whose alarm is the
    # liveness signal (declared in mini-app-polis/infra).
    # No Sentry DSN field: both pipelines report to the one project the
    # worker initialises at import, from SENTRY_DSN_COGS.

    # --- API integration (per ecosystem-standards CD-012) ---
    # ``mini_app_polis.api.KaianoApiClient`` reads the base URL for this
    # environment itself (with no fallback to production), so it is not a
    # setting here. The client presents this cog's named API key
    # directly; there is no token exchange.
    transcription_cog_api_key: str | None = Field(
        default=None,
        description=(
            "This cog's own named API key. The shared client reads it as "
            "TRANSCRIPTION_COG_API_KEY and presents it directly; the API "
            "matches it to identify this cog. Required in production."
        ),
    )

    # --- Behavior ---
    archive_retention_days: int = Field(
        default=7,
        ge=1,
        description=(
            "Days to keep audio in processed/ before the sweep trashes "
            "it. Trashed, not deleted: the shared drive empties its own "
            "trash after ~30 days, so the real floor is this plus that. "
            "7 days covers the typical 'I want to re-listen to that "
            "note from earlier this week' use case; older audio "
            "is rarely consulted and the task on the board is the "
            "durable record."
        ),
    )
    # The sweep runs after every note, so these two cap one run's share
    # of it: what is left over is the next run's, oldest first. Each note
    # archives one file, so a file cap of 2 or more catches up rather than
    # merely keeping pace.
    cleanup_max_buckets_per_run: int = Field(
        default=3,
        ge=1,
        description=(
            "Most expired date folders one retention sweep opens: one Drive "
            "listing each, plus trashing the folder once it is drained. "
            "Dated folders go oldest first. Undated legacy monthly folders "
            "are never trashed, so every sweep opens them again; they go "
            "after the dated ones, and any beyond this cap wait for a sweep "
            "with no dated backlog."
        ),
    )
    cleanup_max_files_per_run: int = Field(
        default=5,
        ge=1,
        description=(
            "Most archived files one retention sweep tries to trash, oldest "
            "first. A folder with files left over is not trashed this run; "
            "the next sweep carries on where this one stopped."
        ),
    )

    # --- Operational delays (TEST-013) ---
    # HTTP client default timeout for outbound API calls (Drive,
    # etc.). Surfaced in settings so tests can override to a
    # tiny value without monkeypatching, per TEST-013.
    http_timeout_seconds: float = Field(
        default=10.0,
        ge=0.0,
        description="Default HTTP timeout for outbound API calls.",
    )

    # --- LLM request timeouts ---
    # One voice note must fit in one Lambda invocation (900 s), SDK
    # retries included, with room for Drive, the task request and the retention
    # sweep. Without a timeout the SDKs default to ~600 s per request.
    #
    # Budget, every request running to its timeout: Whisper at 240 s × 2
    # attempts (max_retries=1 on the client) plus the SDK's wait between
    # them (it honours Retry-After up to 120 s) is 600 s. Claude at 60 s × 2
    # attempts (max_retries=1) plus a wait of at most
    # claude_retry_wait_max_seconds, twice when the first answer does not
    # parse, is 260 s. The task request, through the shared API client, is
    # ~100 s: 3 attempts × 30 s and the waits between them.
    # That is ~960 s before Drive, whose client library gives each
    # request 60 s: 9 requests for the note on a cold container, and
    # 1 + 2 × cleanup_max_buckets_per_run + cleanup_max_files_per_run for
    # the sweep. So the sum fits only while most of it is healthy; the
    # worker stops a run 30 s before the timeout so it still reports.
    # A run that does not fit is not killed: the shared run deadline the
    # worker sets stops the library's Drive retries
    # before that stop, and the stop leaves room for the report. A run that
    # comes close is flagged by a duration alarm declared in
    # mini-app-polis/infra.
    claude_request_timeout_seconds: float = Field(
        default=60.0,
        ge=1.0,
        description=(
            "Per-HTTP-request timeout (seconds) for Anthropic SDK calls "
            "from claude_client.py. The extraction prompt is small and "
            "the response is capped at ~512 tokens, so production calls "
            "are usually <10 s; 60 s is a generous ceiling that still "
            "bounds the worker's exposure during a redeploy. Bump only "
            "if healthy traffic starts timing out."
        ),
    )
    claude_retry_wait_max_seconds: float = Field(
        default=10.0,
        ge=0.0,
        description=(
            "Longest wait (seconds) before the Anthropic SDK's one retry. "
            "The SDK waits whatever a Retry-After header asks, with no "
            "ceiling of its own, which would leave the run's length to the "
            "server; capped here, a retry that comes too early fails and the "
            "queue redelivers the job instead. Part of the budget above."
        ),
    )
    whisper_request_timeout_seconds: float = Field(
        default=240.0,
        ge=1.0,
        description=(
            "Per-HTTP-request timeout (seconds) for OpenAI Whisper SDK "
            "calls from whisper_client.py. Whisper processes audio in "
            "proportion to length; voice notes are typically <5 min of "
            "audio. Part of the one-invocation budget above."
        ),
    )
    environment: str = Field(default="production")


@lru_cache
def get_settings() -> Settings:
    """Cached settings accessor.

    Why a function and not a module-level Settings()? So that tests can
    override env vars and clear the cache between cases:
        from transcription_cog.voicenotes.config import get_settings
        get_settings.cache_clear()
    """
    return Settings()  # type: ignore[call-arg]


# Convenience alias for production code.
settings = get_settings()


# Fields that MUST be populated for voicenotes mode to run. The pieces
# of state the voicenotes pipeline can't fake: an OpenAI key for
# Whisper, the Anthropic key the extraction call in
# clients/claude_client.py uses, and the Drive folder that's the source
# of audio. The task itself is requested through the API with this cog's
# own key, which the API client checks for itself.
_VOICENOTES_REQUIRED_FIELDS: tuple[str, ...] = (
    "openai_api_key",
    "anthropic_api_key",
    "google_drive_voice_inbox_folder_id",
)


def require_voicenotes_settings(cfg: Settings | None = None) -> Settings:
    """Validate that all voicenotes-mode required fields are populated.

    Called at the entry of every voicenotes flow (``voicenotes_ingest``,
    ``voicenotes_cleanup``). Module import deliberately accepts empty
    defaults so the parent cog can boot and serve other modes without
    voicenotes secrets — see module docstring. This guard moves the
    failure from "opaque pydantic ValidationError at boot" to "loud
    RuntimeError when voicenotes mode is invoked, listing every
    missing env var."

    Parameters
    ----------
    cfg:
        Optional ``Settings`` instance. Defaults to the module-level
        singleton; tests pass a fresh instance after clearing the
        ``get_settings`` cache.

    Returns
    -------
    The validated ``Settings`` instance (so callers can chain).

    Raises
    ------
    RuntimeError
        If one or more required fields are empty. Message enumerates
        all missing fields plus the canonical env-var names so the
        operator can fix Doppler in one pass.
    """
    cfg = cfg or settings
    missing = [name for name in _VOICENOTES_REQUIRED_FIELDS if not getattr(cfg, name)]
    if missing:
        env_names = ", ".join(name.upper() for name in missing)
        raise RuntimeError(
            "voicenotes mode invoked but required configuration is missing. "
            f"Populate the following in Doppler and declare them in mini-app-polis/infra: "
            f"{env_names}. "
            "See .env.example for descriptions, or "
            "docs/decisions/ADR-0004-voicenotes-merge.md for the merge context."
        )
    return cfg
