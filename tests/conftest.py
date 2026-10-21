"""Shared pytest configuration for transcription-cog.

Project-wide test bootstrap (env vars) lives at module scope below. It
MUST run before any test file is collected: the voicenotes sub-package's
``Settings`` (pydantic-settings) is built at module import, and pytest
collects ``tests/transcription_cog/`` — which imports the worker, and
through it the voicenotes flows — before ``tests/voicenotes/conftest.py``
ever loads.
"""

from __future__ import annotations

import os

# ---------------------------------------------------------------------------
# Env-var bootstrap — MUST run before any voicenotes/transcription_cog import.
# ---------------------------------------------------------------------------

_TEST_ENV_DEFAULTS = {
    # Required by voicenotes/config.py Settings
    "OPENAI_API_KEY": "test-openai-key",
    "ANTHROPIC_API_KEY": "test-anthropic-key",
    "GOOGLE_DRIVE_VOICE_INBOX_FOLDER_ID": "test-folder-id",
    "KAIANO_API_BASE_URL": "https://api.test.invalid",
    "TRANSCRIPTION_COG_API_KEY": "test-api-key",
    "ENVIRONMENT": "test",
}

for _key, _default in _TEST_ENV_DEFAULTS.items():
    os.environ.setdefault(_key, _default)


import pytest  # noqa: E402


@pytest.fixture(autouse=True)
def _production_by_default(monkeypatch: pytest.MonkeyPatch) -> None:
    """Exercise the real reporting path unless a test says otherwise.

    Effect gates resolve from the environment, and the module-scope
    bootstrap seeds ENVIRONMENT=test — which would suppress every run
    report and quietly turn reporting tests into a test of the
    suppression branch. Tests that want the suppressed path set
    ENVIRONMENT themselves.
    """
    monkeypatch.setenv("ENVIRONMENT", "production")
