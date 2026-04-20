"""Shared pytest configuration for notes-ingest-cog.

Isolates every test from the real Prefect Cloud account and local
~/.prefect/storage by routing all flow/task execution through an
ephemeral SQLite-backed test harness. Also disables task retries and
quiets Prefect's logger so expected exceptions (e.g. the duplicate
drive_file_id path in flow.py) don't dump tracebacks on every run.
"""

from __future__ import annotations

import logging

import pytest
from prefect.testing.utilities import prefect_test_harness


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
def _quiet_prefect_logs() -> None:
    """Suppress Prefect's task-engine tracebacks for expected exceptions."""
    logging.getLogger("prefect").setLevel(logging.CRITICAL)
    logging.getLogger("prefect.task_runs").setLevel(logging.CRITICAL)
    logging.getLogger("prefect.flow_runs").setLevel(logging.CRITICAL)
