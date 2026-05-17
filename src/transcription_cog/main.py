"""Application entrypoint for transcription-cog.

Registers a single router-style Prefect deployment (``transcription-cog/
transcription-cog``) and starts a runner loop that polls for scheduled or
manually triggered runs.

The router flow dispatches to one of the underlying production flows
based on the ``mode`` parameter. Callers (watcher-cog, Prefect UI, REST
API, CLI) must pass ``mode`` explicitly; an unknown or missing mode
raises ValueError.

Supported modes:
    - "wcs-transcripts"     → process_transcript (WCS lesson transcripts)
    - "voicenotes"          → voicenotes_ingest (voice sticky-note pipeline)
    - "voicenotes-cleanup"  → voicenotes_cleanup (manual operator sweep)

Railway start command: python -m transcription_cog.main

All flows run in-process on Railway with full access to environment
variables. No work pool required.

On Railway restart, any in-flight runs are interrupted and Prefect
Cloud marks them as crashed. The on_crashed hooks in each underlying
flow handle crash reporting to api-kaianolevine-com automatically.

Observability layers:
  L1 — Healthchecks.io: pinged on startup
  L2 — Structured logs: mini_app_polis logger throughout
  L3 — Sentry: captures all unhandled exceptions and invalid filename warnings

History
-------
The ``voicenotes`` and ``voicenotes-cleanup`` modes were merged in
from the standalone ``voicenotes-cog`` repository in May 2026 so the
two pipelines share a single Prefect deployment and Railway service.
See ``docs/decisions/ADR-004-voicenotes-merge.md`` and the ``voicenotes/``
sub-package.
"""

from __future__ import annotations

import os
import sys
from typing import Any, Literal

import httpx
import sentry_sdk
from dotenv import load_dotenv
from mini_app_polis import logger as log
from prefect import flow, get_run_logger, serve

from transcription_cog.config import load_config
from transcription_cog.flow import process_transcript
from transcription_cog.voicenotes.flows.cleanup import voicenotes_cleanup
from transcription_cog.voicenotes.flows.ingest import voicenotes_ingest

load_dotenv()

LOG = log.get_logger()


def _get_run_logger():
    """Dual logger per PIPE-006: Prefect run logger inside a flow context,
    stdlib fallback outside.

    The name contains "logger" and the body calls ``get_run_logger()``,
    which is the wrapper pattern PIPE-006 accepts. Direct
    ``get_run_logger()`` calls in a flow body raise
    ``MissingContextError`` when the flow function is invoked outside
    Prefect orchestration (e.g. from unit tests that call the router
    directly), so we wrap it.
    """
    try:
        return get_run_logger()
    except Exception:
        return LOG


#: Supported router modes. Declared as a Literal so Prefect Cloud's
#: "Custom Run" UI renders a dropdown (via the auto-generated JSON-schema
#: enum) instead of a free-form string field. Adding a new mode?
#:   1) add the string here,
#:   2) add it to _MODE_DISPATCH,
#:   3) document it in the module docstring.
NotesIngestMode = Literal["wcs-transcripts", "voicenotes", "voicenotes-cleanup"]

# Map of supported router modes to the underlying flow functions.
_MODE_DISPATCH: dict[str, Any] = {
    "wcs-transcripts": process_transcript,
    "voicenotes": voicenotes_ingest,
    "voicenotes-cleanup": voicenotes_cleanup,
}


@flow(name="transcription-cog")
def notes_ingest_router(mode: NotesIngestMode) -> Any:
    """Single entrypoint flow that dispatches to a sub-flow by ``mode``.

    Parameters
    ----------
    mode:
        Which underlying flow to run. Required. One of:
        ``"wcs-transcripts"``, ``"voicenotes"``, ``"voicenotes-cleanup"``.

    Raises
    ------
    ValueError
        If ``mode`` somehow reaches the body without being a recognized
        dispatch key. The Literal annotation should already prevent this
        at Prefect's parameter-validation layer, but we keep the runtime
        guard so the flow never silently no-ops.
    """
    # PIPE-006: every Prefect flow body must surface a get_run_logger()
    # call (directly or via a logger-wrapper). _get_run_logger() is the
    # accepted wrapper form — it calls Prefect's get_run_logger() inside
    # a flow context and falls back to the stdlib module logger when
    # tests invoke the router directly without an active context.
    logger = _get_run_logger()
    logger.info("transcription-cog router dispatching mode=%s", mode)

    target = _MODE_DISPATCH.get(mode)
    if target is None:
        raise ValueError(
            f"Unknown transcription-cog router mode: {mode!r}. "
            f"Supported modes: {sorted(_MODE_DISPATCH)}"
        )

    return target()


def _init_sentry(dsn: str) -> None:
    if not dsn:
        LOG.warning(
            log.with_log_prefix(
                log.LOG_WARNING,
                "SENTRY_DSN not set — error tracking disabled",
            )
        )
        return
    sentry_sdk.init(
        dsn=dsn,
        traces_sample_rate=0.1,
        environment=os.getenv("RAILWAY_ENVIRONMENT", "production"),
        release=os.getenv("RAILWAY_GIT_COMMIT_SHA", "unknown"),
    )
    LOG.info(log.with_log_prefix(log.LOG_SUCCESS, "Sentry initialised"))


def _ping_healthcheck(url: str, timeout_seconds: int) -> None:
    if not url:
        return
    try:
        httpx.get(url, timeout=timeout_seconds)
    except Exception as exc:
        LOG.warning(
            log.with_log_prefix(log.LOG_WARNING, f"Healthchecks.io ping failed: {exc}")
        )


def main() -> None:
    """Register the transcription-cog router deployment and serve in-process.

    ``prefect.serve`` blocks the process and listens for ad-hoc
    triggers from watcher-cog. The deployment has no cron — both
    pipelines are watcher-triggered.
    """
    LOG.info(log.with_log_prefix(log.LOG_START, "transcription-cog starting"))

    try:
        cfg = load_config()
    except RuntimeError as exc:
        LOG.error(log.with_log_prefix(log.LOG_FAILURE, f"Config error: {exc}"))
        sys.exit(1)

    _init_sentry(cfg.sentry_dsn)
    _ping_healthcheck(cfg.healthchecks_url, cfg.healthcheck_timeout_seconds)

    LOG.info(
        log.with_log_prefix(
            log.LOG_START,
            (
                f"Serving transcription-cog router — modes={sorted(_MODE_DISPATCH)} "
                f"provider={cfg.llm_provider} model={cfg.llm_model}"
            ),
        )
    )

    serve(
        notes_ingest_router.to_deployment(
            name="transcription-cog",
            description=(
                "Router for transcription-cog. Triggered ad-hoc by "
                "watcher-cog with `mode` set to one of: "
                "'wcs-transcripts', 'voicenotes', 'voicenotes-cleanup'. "
                "Single deployment shared across both pipelines."
            ),
            concurrency_limit=1,
            tags=["transcription-cog"],
        ),
    )


if __name__ == "__main__":
    try:
        main()
    except KeyboardInterrupt:
        sys.exit(0)
