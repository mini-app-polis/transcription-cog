"""Shared pytest configuration for transcription-cog.

Isolates every test from the real Prefect Cloud account and local
~/.prefect/storage by routing all flow/task execution through an
ephemeral SQLite-backed test harness. Also disables task retries and
quiets Prefect's logger so expected exceptions (e.g. the duplicate
drive_file_id path in flow.py) don't dump tracebacks on every run.

Project-wide test bootstrap (env vars + concurrency stub) lives at
module scope below. It MUST run before any test file is collected,
because pytest collects test modules alphabetically and any file that
imports ``transcription_cog.main`` or ``transcription_cog.voicenotes``
materialises ``@task(retries=settings.task_retries, ...)`` at import
time — pinning the retry count for the rest of the session before the
voicenotes sub-conftest's bootstrap has a chance to run.
"""

from __future__ import annotations

import logging
import os

# ---------------------------------------------------------------------------
# Env-var bootstrap — MUST run before any voicenotes/transcription_cog import.
#
# The voicenotes sub-package's ``Settings`` (pydantic-settings) and its
# ``@task`` decorators read these at module load. Pytest collects tests
# alphabetically, so ``tests/transcription_cog/`` modules can trigger a
# voicenotes import (via transcription_cog.main → voicenotes.flows) BEFORE
# ``tests/voicenotes/conftest.py`` ever loads. Seeding defaults at the
# top-level conftest module scope is the only place early enough.
#
# Test mode forces retry counts to 0 + delays to [] so failing tasks
# don't introduce real wall-clock waits.
# ---------------------------------------------------------------------------

_TEST_ENV_DEFAULTS = {
    # Required by voicenotes/config.py Settings
    "OPENAI_API_KEY": "test-openai-key",
    "ANTHROPIC_API_KEY": "test-anthropic-key",
    "ASANA_ACCESS_TOKEN": "test-asana-token",
    "ASANA_WORKSPACE_ID": "test-workspace-gid",
    "ASANA_INBOX_PROJECT_ID": "test-project-gid",
    "ASANA_INBOX_SECTION_ID": "test-section-gid",
    "GOOGLE_DRIVE_VOICE_INBOX_FOLDER_ID": "test-folder-id",
    "KAIANO_API_BASE_URL": "https://api.test.invalid",
    "TRANSCRIPTION_COG_API_KEY": "test-api-key",
    "ENVIRONMENT": "test",
    # Defense-in-depth against slow tests in the voicenotes sub-pipeline:
    "TASK_RETRIES": "0",
    "TASK_RETRY_DELAYS_SECONDS": "[]",
    "EXTRACT_TASK_RETRIES": "0",
    "EXTRACT_TASK_RETRY_DELAYS_SECONDS": "[]",
}

for _key, _default in _TEST_ENV_DEFAULTS.items():
    os.environ.setdefault(_key, _default)


# ---------------------------------------------------------------------------
# Patch Prefect's runtime concurrency slot acquisition with a no-op.
# Must run before any voicenotes flow module imports it, otherwise the
# flow body would block trying to acquire a slot from the test harness's
# in-memory implementation.
# ---------------------------------------------------------------------------

from contextlib import contextmanager  # noqa: E402


@contextmanager
def _noop_concurrency(*args, **kwargs):
    yield


import prefect.concurrency.sync as _prefect_conc  # noqa: E402

_prefect_conc.concurrency = _noop_concurrency  # type: ignore[assignment]


# Also patch the consumer's local binding. ``flow.py`` does
# ``from prefect.concurrency.sync import concurrency``, which captures
# the original function into its own namespace at import time. Patching
# only the source module ``prefect.concurrency.sync`` is too late for
# code that has already executed that ``from ... import ...`` line —
# each test invocation would still call the real concurrency primitive,
# which round-trips to the harness's in-memory slot table and costs
# ~1 second per call. Replace flow.py's bound symbol directly so the
# no-op takes effect regardless of import order.
import transcription_cog.flow as _tcflow_mod  # noqa: E402

_tcflow_mod.concurrency = _noop_concurrency  # type: ignore[assignment]


# ---------------------------------------------------------------------------
# Imports below this line are safe — env vars set, concurrency neutered.
# ---------------------------------------------------------------------------

import pytest  # noqa: E402
from prefect.testing.utilities import prefect_test_harness  # noqa: E402


@pytest.fixture(autouse=True, scope="session")
def prefect_test_fixture():
    """Route all Prefect orchestration through an ephemeral test backend."""
    with prefect_test_harness():
        yield


@pytest.fixture(autouse=True)
def _disable_task_retries(monkeypatch: pytest.MonkeyPatch) -> None:
    """Disable task retries in tests.

    flow.py declares @task(retries=2) on every task. In production this
    is correct; in tests it means a mocked side_effect exception fires
    three times per call, generating noise and slowing the suite.
    """
    monkeypatch.setenv("PREFECT_TASK_DEFAULT_RETRIES", "0")
    monkeypatch.setenv("PREFECT_TASK_DEFAULT_RETRY_DELAY_SECONDS", "0")


@pytest.fixture(autouse=True)
def _production_by_default(monkeypatch: pytest.MonkeyPatch) -> None:
    """Exercise the real Healthchecks path unless a test says otherwise.

    Effect gates resolve from the environment, and the module-scope
    bootstrap seeds ENVIRONMENT=test — which would suppress every ping
    and quietly turn healthcheck tests into a test of the suppression
    branch. Tests that want the suppressed path set ENVIRONMENT themselves.
    """
    monkeypatch.setenv("ENVIRONMENT", "production")
    monkeypatch.delenv("HEALTHCHECKS_ENABLED", raising=False)


@pytest.fixture(autouse=True)
def _quiet_prefect_logs() -> None:
    """Suppress Prefect's task-engine tracebacks for expected exceptions."""
    logging.getLogger("prefect").setLevel(logging.CRITICAL)
    logging.getLogger("prefect.task_runs").setLevel(logging.CRITICAL)
    logging.getLogger("prefect.flow_runs").setLevel(logging.CRITICAL)
