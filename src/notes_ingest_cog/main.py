"""Entry point for notes-ingest-cog.

Initialises Sentry, connects to Prefect Cloud, and serves the
process_transcript flow. Railway keeps this process alive indefinitely.

Observability layers:
  L1 — Healthchecks.io: pinged after each served deployment cycle
  L2 — Structured logs: mini_app_polis logger throughout
  L3 — Sentry: captures all unhandled exceptions
"""

from __future__ import annotations

import os
import sys

import httpx
import sentry_sdk
from dotenv import load_dotenv
from mini_app_polis import logger as log
from prefect import serve

from .config import load_config
from .flow import process_transcript

load_dotenv()

LOG = log.get_logger()


def _init_sentry(dsn: str) -> None:
    if not dsn:
        LOG.warning(
            log.with_log_prefix(
                log.LOG_WARNING, "SENTRY_DSN not set — error tracking disabled"
            )
        )
        return
    sentry_sdk.init(
        dsn=dsn,
        traces_sample_rate=0.1,
        environment=os.getenv("RAILWAY_ENVIRONMENT", "production"),
    )
    LOG.info(log.with_log_prefix(log.LOG_SUCCESS, "Sentry initialised"))


def _ping_healthcheck(url: str) -> None:
    if not url:
        return
    try:
        httpx.get(url, timeout=5)
    except Exception as exc:
        LOG.warning(
            log.with_log_prefix(log.LOG_WARNING, f"Healthchecks.io ping failed: {exc}")
        )


def main() -> None:
    LOG.info(log.with_log_prefix(log.LOG_START, "notes-ingest-cog starting"))

    try:
        cfg = load_config()
    except RuntimeError as exc:
        LOG.error(log.with_log_prefix(log.LOG_FAILURE, f"Config error: {exc}"))
        sys.exit(1)

    _init_sentry(cfg.sentry_dsn)
    _ping_healthcheck(cfg.healthchecks_url)

    LOG.info(
        log.with_log_prefix(
            log.LOG_START,
            f"Serving process_transcript flow — provider={cfg.llm_provider} model={cfg.llm_model}",
        )
    )

    # prefect.serve() blocks — Prefect Cloud dispatches flow runs to this process
    serve(
        process_transcript.to_deployment(
            name="notes-ingest-cog",
            concurrency_limit=1,
        ),
    )


if __name__ == "__main__":
    main()
