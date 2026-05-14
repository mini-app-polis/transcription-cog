"""Shared pytest fixtures for voicenotes-cog.

Patterns:
  - Test env vars seeded BEFORE the cog imports so pydantic-settings
    validation succeeds without Doppler.
  - Retry counts forced to ZERO and retry delays to [] so failure-path
    tests don't sleep through Prefect's exponential backoff.
  - Prefect's ``concurrency()`` context manager replaced with a no-op
    so tests don't try to acquire a slot from a real Prefect server.
  - ``time.sleep`` is patched per-test as defense in depth — unit
    tests have no business waiting on the wall clock.
  - prefect_test_harness gives us in-memory Prefect.
  - Singletons reset between tests so injected mocks take effect.
"""

from __future__ import annotations

import logging
import os

# ---------------------------------------------------------------------------
# Silence chatty third-party DEBUG logs in test output.
#
# The Prefect test harness spins up a temporary local server, which
# triggers a lot of httpcore/httpx/websockets/asyncio DEBUG chatter on
# every connect attempt before the server is ready. Graphviz emits a
# bank of deprecation banners on import. None of it is signal for cog
# test failures, and the noise drowns out actual cog log lines when a
# test does fail. Cap each of these libraries at WARNING so genuine
# misbehavior still surfaces.
#
# Lives at module scope (rather than in ``pytest_configure``) so the
# levels are applied as soon as conftest is imported — before any
# fixture or test triggers the noisy library imports.
for _noisy_logger in (
    "httpcore",
    "httpcore.connection",
    "httpcore.http11",
    "httpx",
    "websockets",
    "websockets.client",
    "asyncio",
    "graphviz",
    "graphviz._tools",
):
    logging.getLogger(_noisy_logger).setLevel(logging.WARNING)

# ---------------------------------------------------------------------------
# Env-var bootstrap — MUST run before notes_ingest_cog.voicenotes is imported.
# pydantic-settings validates required fields at module load.
# Test mode also forces retry counts to 0 + delays to [] so failing
# tasks don't introduce real wall-clock waits.
# ---------------------------------------------------------------------------

_TEST_ENV_DEFAULTS = {
    "OPENAI_API_KEY": "test-openai-key",
    "ANTHROPIC_API_KEY": "test-anthropic-key",
    "TODOIST_API_TOKEN": "test-todoist-token",
    "TODOIST_INBOX_PROJECT_ID": "test-project-id",
    "GOOGLE_DRIVE_VOICE_INBOX_FOLDER_ID": "test-folder-id",
    "KAIANO_API_BASE_URL": "https://api.test.invalid",
    "KAIANO_API_CLERK_MACHINE_SECRET": "test-machine-secret",
    "ENVIRONMENT": "test",
    # Defense-in-depth against slow tests:
    "TASK_RETRIES": "0",
    "TASK_RETRY_DELAYS_SECONDS": "[]",
    "EXTRACT_TASK_RETRIES": "0",
    "EXTRACT_TASK_RETRY_DELAYS_SECONDS": "[]",
}

for _key, _default in _TEST_ENV_DEFAULTS.items():
    os.environ.setdefault(_key, _default)


# ---------------------------------------------------------------------------
# Patch Prefect's runtime concurrency slot acquisition with a no-op
# BEFORE any cog module imports it. Otherwise the flow body would try
# to call out to a Prefect server (or block on the test harness's
# in-memory implementation, which is slower than skipping it entirely).
# ---------------------------------------------------------------------------

from contextlib import contextmanager  # noqa: E402


@contextmanager
def _noop_concurrency(*args, **kwargs):
    yield


# Patch both the source and the import site used by our flows.
import prefect.concurrency.sync as _prefect_conc  # noqa: E402

_prefect_conc.concurrency = _noop_concurrency  # type: ignore[assignment]


# ---------------------------------------------------------------------------
# Imports below this line are safe — env vars set, concurrency neutered.
# ---------------------------------------------------------------------------

import time  # noqa: E402
from collections.abc import Iterator  # noqa: E402
from datetime import date  # noqa: E402

import pytest  # noqa: E402

# Re-import flows AFTER patching so they bind to the no-op.
from notes_ingest_cog.voicenotes.clients import (  # noqa: E402
    claude_client as _claude_mod,
)
from notes_ingest_cog.voicenotes.clients import drive_client as _drive_mod  # noqa: E402
from notes_ingest_cog.voicenotes.clients import (  # noqa: E402
    todoist_client as _todoist_mod,
)
from notes_ingest_cog.voicenotes.clients import (  # noqa: E402
    whisper_client as _whisper_mod,
)
from notes_ingest_cog.voicenotes.flows import cleanup as _cleanup_mod  # noqa: E402
from notes_ingest_cog.voicenotes.flows import ingest as _ingest_mod  # noqa: E402

# Force the patched concurrency into the flow modules' namespaces too,
# in case they captured the original symbol before this conftest ran
# (e.g., when pytest collects from a different starting point).
_ingest_mod.concurrency = _noop_concurrency  # type: ignore[assignment]
_cleanup_mod.concurrency = _noop_concurrency  # type: ignore[assignment]


# NB: ``prefect_test_harness`` is provided session-wide by the parent
# ``tests/conftest.py`` (``prefect_test_fixture``). We deliberately do NOT
# re-enter it here — two concurrent harnesses in the same session race
# on the temporary backend and break unrelated tests.


@pytest.fixture(autouse=True)
def _no_real_sleeps(monkeypatch) -> None:
    """Belt-and-braces: replace ``time.sleep`` with a no-op.

    Nothing in our code under test should sleep, but Prefect or a
    transitive lib might. A unit test that takes more than a second
    is almost certainly waiting on something it shouldn't be.
    """
    monkeypatch.setattr(time, "sleep", lambda *_args, **_kwargs: None)


@pytest.fixture(autouse=True)
def _reset_client_singletons() -> Iterator[None]:
    """Reset every client singleton between tests.

    Tests that inject mocks via the singleton accessors expect the
    cache to be empty at the start of each test.
    """
    _whisper_mod.reset_whisper_client()
    _claude_mod.reset_claude_client()
    _todoist_mod.reset_todoist_client()
    _drive_mod.reset_drive_client()
    yield
    _whisper_mod.reset_whisper_client()
    _claude_mod.reset_claude_client()
    _todoist_mod.reset_todoist_client()
    _drive_mod.reset_drive_client()


@pytest.fixture
def sample_transcript_simple() -> str:
    """A clean, normal transcript."""
    return "remind me to send the floor trials report to Mark"


@pytest.fixture
def sample_transcript_with_due() -> str:
    """A transcript with an explicit 'by' date phrase."""
    return "send the floor trials report to Mark by Friday"


@pytest.fixture
def sample_transcript_empty() -> str:
    """An empty-string transcript (Whisper returns ``""`` on silent audio)."""
    return ""


@pytest.fixture
def sample_transcript_gibberish() -> str:
    """A non-actionable transcript that should trigger needs_review=True."""
    return "krsht mmphnkk drrr the the the"


@pytest.fixture
def fixed_today() -> date:
    """A fixed reference date for deterministic due_date tests."""
    return date(2026, 5, 8)
