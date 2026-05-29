"""Configuration for voicenotes sub-pipeline.

All runtime config flows through pydantic-settings. Doppler injects env vars;
this module just reads them. Defaults are deliberately conservative.

Validation-deferral note
------------------------
The voicenotes-specific required fields (``openai_api_key``,
``todoist_api_token``, ``todoist_inbox_project_id``,
``google_drive_voice_inbox_folder_id``) are declared with empty-string
defaults rather than ``Field(...)``-required. This is deliberate:
transcription-cog ships as a single Railway service hosting two
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
    settings.todoist_inbox_project_id
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
        env_file=".env",
        env_file_encoding="utf-8",
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
    # Empty-string defaults: see module docstring (validation deferred).
    todoist_api_token: str = Field(
        default="",
        description=(
            "Long-lived Todoist personal API token. Required for voicenotes "
            "mode. Bootstrap via scripts/setup_todoist.py."
        ),
    )
    todoist_inbox_project_id: str = Field(
        default="",
        description=(
            "Todoist project ID where voice notes are posted. Required for "
            "voicenotes mode."
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
    # ``list[float]`` (not ``list[int]``) because Prefect's
    # ``@task(retry_delay_seconds=...)`` typing is ``list[float] | ...``
    # and ``list[int]`` is invariant in PEP-484 — mypy refuses the
    # assignment. Pydantic coerces ``[5, 15, 30]`` from env into floats
    # transparently, and Prefect treats integer-valued floats the same
    # as ints at runtime, so this is a zero-cost type fix.
    task_retry_delays_seconds: list[float] = Field(
        default=[5.0, 15.0, 30.0],
        description=(
            "Per-attempt retry delay (seconds) for ingest tasks. "
            "Length should match task_retries."
        ),
    )
    extract_task_retries: int = Field(
        default=5,
        ge=0,
        description=(
            "Retry count for the Claude extraction task. "
            "Sized to ride out Anthropic 529 'overloaded' capacity "
            "blips, which can persist for several minutes — short "
            "retry budgets here drop voice notes on the floor."
        ),
    )
    extract_task_retry_delays_seconds: list[float] = Field(
        default=[30.0, 60.0, 120.0, 300.0, 600.0],
        description=(
            "Retry delays (seconds) for the Claude extraction task. "
            "Exponential-ish backoff from 30s up to 10 minutes, "
            "totaling ~18min across 5 retries. Length matches "
            "extract_task_retries; Prefect reuses the last delay if "
            "the list is shorter than retries."
        ),
    )

    # HTTP client default timeout for outbound API calls (Todoist,
    # Drive, etc.). Surfaced in settings so tests can override to a
    # tiny value without monkeypatching, per TEST-013.
    http_timeout_seconds: float = Field(
        default=10.0,
        ge=0.0,
        description="Default HTTP timeout for outbound API calls.",
    )

    # --- LLM request + task timeouts ---
    # Two layers protect against the "deploy mid-LLM-call" hang. Without
    # these, a redeploy that lands while a worker is waiting on an LLM
    # response leaves the old process holding the socket open up to the
    # Anthropic/OpenAI SDK default of ~600 s (10 min), well past
    # Railway's SIGKILL grace window — so the run is force-killed in an
    # unclean state instead of cooperatively shutting down. The Prefect
    # task timeout on top lets Prefect cancel a hung task even if the
    # SDK retries internally.
    #
    # Layering: request < task. The per-request value caps one HTTP
    # call; the per-task value caps the whole attempt including SDK
    # internal retries. The Anthropic/OpenAI SDKs default to ~2
    # internal retries, so task ≈ request × (1 + SDK retries) leaves
    # room for one retry plus headroom.
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
    whisper_request_timeout_seconds: float = Field(
        default=300.0,
        ge=1.0,
        description=(
            "Per-HTTP-request timeout (seconds) for OpenAI Whisper SDK "
            "calls from whisper_client.py. Whisper processes audio in "
            "proportion to length; voice notes are typically <5 min of "
            "audio, but operators occasionally record longer dictation. "
            "5 min covers the realistic upper bound without leaving the "
            "worker socket open indefinitely."
        ),
    )
    claude_task_timeout_seconds: float = Field(
        default=180.0,
        ge=1.0,
        description=(
            "Prefect task timeout (seconds) for the extract task. Lets "
            "Prefect cancel a stuck task even if the Anthropic SDK is "
            "internally retrying. Sized at ~3× claude_request to allow "
            "one SDK retry cycle plus headroom."
        ),
    )
    whisper_task_timeout_seconds: float = Field(
        default=600.0,
        ge=1.0,
        description=(
            "Prefect task timeout (seconds) for the transcribe task. "
            "Sized at ~2× whisper_request — one SDK retry on top of the "
            "longest legitimate audio file we expect. Long enough to "
            "succeed on real traffic, short enough to free the worker "
            "for the next attempt before a redeploy stalls the pipeline."
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


# Fields that MUST be populated for voicenotes mode to run. The four
# pieces of state the voicenotes pipeline can't fake: an OpenAI key for
# Whisper, the two Todoist credentials needed to post a task, and the
# Drive folder that's the source of audio. ``anthropic_api_key`` is
# deliberately omitted — the parent WCS pipeline has its own
# Claude-via-mini_app_polis path that reads ANTHROPIC_API_KEY too, so
# its absence will already be caught upstream if it matters.
_VOICENOTES_REQUIRED_FIELDS: tuple[str, ...] = (
    "openai_api_key",
    "anthropic_api_key",
    "todoist_api_token",
    "todoist_inbox_project_id",
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
            f"Populate the following env vars in Doppler / Railway: {env_names}. "
            "See .env.example for descriptions, or "
            "docs/decisions/ADR-004-voicenotes-merge.md for the merge context."
        )
    return cfg
