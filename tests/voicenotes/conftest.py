"""Voicenotes sub-package test fixtures.

Per-test concerns only — singleton resets between tests, the
deterministic transcript fixtures, and a defense-in-depth no-sleep
patch. The project-wide setup (env-var bootstrap, Prefect concurrency
stub, test harness) lives in the parent ``tests/conftest.py`` so it
fires before pytest collects ANY test module, regardless of which
subdirectory pytest enters first. See parent conftest for rationale.

The only thing this conftest still does at module scope is quiet a few
chatty third-party loggers — kept here (rather than in the parent)
because they're a voicenotes-test concern (the Prefect test harness's
connect chatter and graphviz's banners drown out cog log lines on
failure-path tests in this sub-package specifically).
"""

from __future__ import annotations

import logging
import time
from collections.abc import Iterator
from contextlib import contextmanager
from datetime import date

import pytest

# Silence chatty third-party DEBUG logs in test output. Capped at module
# scope so the levels are applied as soon as this conftest is imported,
# before any fixture or test triggers the noisy library imports.
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


# Local no-op concurrency context — bound into the flow modules below as
# defense in depth in case they captured the original ``concurrency``
# symbol before the parent conftest's monkeypatch landed. (Today the
# parent conftest patches before any voicenotes import, but the
# belt-and-braces local rebinding is cheap.)
@contextmanager
def _noop_concurrency(*args, **kwargs):
    yield


# Imports below pull in voicenotes modules — safe at this point because
# the parent ``tests/conftest.py`` has already seeded env vars and
# patched Prefect's concurrency slot acquisition. E402 fires only
# because the ``_noisy_logger`` loop and the ``_noop_concurrency``
# definition above are deliberate module-scope statements; the imports
# themselves are first-use-correct.
from transcription_cog.voicenotes.clients import (  # noqa: E402
    asana_client as _asana_mod,
)
from transcription_cog.voicenotes.clients import (  # noqa: E402
    claude_client as _claude_mod,
)
from transcription_cog.voicenotes.clients import (  # noqa: E402
    drive_client as _drive_mod,
)
from transcription_cog.voicenotes.clients import (  # noqa: E402
    whisper_client as _whisper_mod,
)
from transcription_cog.voicenotes.flows import cleanup as _cleanup_mod  # noqa: E402
from transcription_cog.voicenotes.flows import ingest as _ingest_mod  # noqa: E402

# Belt-and-braces: rebind concurrency in the flow modules' namespaces in
# case they bound the symbol at import time before parent conftest's
# monkeypatch landed.
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
    _asana_mod.reset_asana_client()
    _drive_mod.reset_drive_client()
    yield
    _whisper_mod.reset_whisper_client()
    _claude_mod.reset_claude_client()
    _asana_mod.reset_asana_client()
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
