"""Healthchecks.io ping gating by environment."""

from __future__ import annotations

from unittest.mock import MagicMock

import pytest

from transcription_cog import main as main_module


def test_ping_healthcheck_calls_get_in_production(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("ENVIRONMENT", "production")
    get = MagicMock()
    monkeypatch.setattr(main_module.httpx, "get", get)

    main_module._ping_healthcheck("https://hc-ping.com/abc", timeout_seconds=5)

    get.assert_called_once_with("https://hc-ping.com/abc", timeout=5)


def test_ping_healthcheck_suppressed_outside_production(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A production ping URL copied into dev must still never be pinged."""
    monkeypatch.setenv("ENVIRONMENT", "development")
    get = MagicMock()
    monkeypatch.setattr(main_module.httpx, "get", get)

    main_module._ping_healthcheck("https://hc-ping.com/abc", timeout_seconds=5)

    get.assert_not_called()
