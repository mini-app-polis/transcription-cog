"""Transcription-cog wrappers around :mod:`mini_app_polis.pipeline_status`.

This module is a thin shim around the shared pipeline-status helpers in
**common-python-utils**. Every Kaiano cog self-reports the outcome of
its runs the same way, and the actual machinery (``post_run_finding``,
``make_failure_hook``, ``get_run_id``, ``get_prefect_logger``) lives in
the shared library so the payload shape and best-effort semantics stay
in sync across cogs.

The shim's only job is to pre-bind ``repo="transcription-cog"`` on
``post_run_finding`` and ``make_failure_hook`` so call sites don't have
to repeat it.

The voicenotes flow inside this package runs under a different repo
identifier (``voicenotes-cog``) and has its own per-row translation
needs, so it imports from :mod:`mini_app_polis.pipeline_status`
directly rather than going through this shim.
"""

from __future__ import annotations

from collections.abc import Callable
from contextlib import AbstractContextManager
from typing import Any

from mini_app_polis.pipeline_status import (
    DeliveryReport,
    RunReport,
    Severity,
    get_prefect_logger,
    get_run_id,
)
from mini_app_polis.pipeline_status import (
    make_failure_hook as _make_failure_hook,
)
from mini_app_polis.pipeline_status import (
    post_run_finding as _post_run_finding,
)
from mini_app_polis.pipeline_status import (
    run_report as _run_report,
)

REPO = "transcription-cog"
"""Repo identifier sent on every self-reported finding from this cog."""


def post_run_finding(
    flow_name: str,
    severity: Severity,
    text: str | None = None,
    *,
    suggestion: str | None = None,
    production_only: bool = True,
    source: str = "flow_inline",
    notable: bool = False,
    **extras: Any,
) -> DeliveryReport:
    """Emit one self-reported finding for this transcription-cog run.

    Identical to :func:`mini_app_polis.pipeline_status.post_run_finding`
    except that ``repo`` is pre-bound to ``"transcription-cog"``.
    """
    return _post_run_finding(
        flow_name,
        severity,
        text,
        repo=REPO,
        suggestion=suggestion,
        production_only=production_only,
        source=source,
        notable=notable,
        **extras,
    )


def make_failure_hook(
    flow_name: str,
    *,
    production_only: bool = True,
) -> Callable[..., None]:
    """Return a Prefect ``on_failure`` / ``on_crashed`` hook for this cog.

    Pre-binds ``repo="transcription-cog"`` on the library helper.
    """
    return _make_failure_hook(flow_name, repo=REPO, production_only=production_only)


def run_report(
    flow_name: str,
    *,
    production_only: bool = True,
    notable: bool = False,
    source: str = "flow_inline",
) -> AbstractContextManager[RunReport]:
    """Open a run report for this cog, with ``repo`` pre-bound.

    Identical to :func:`mini_app_polis.pipeline_status.run_report` except
    that ``repo`` is fixed to ``"transcription-cog"``.
    """
    return _run_report(
        flow_name,
        repo=REPO,
        production_only=production_only,
        notable=notable,
        source=source,
    )


__all__ = [
    "REPO",
    "RunReport",
    "Severity",
    "get_prefect_logger",
    "get_run_id",
    "make_failure_hook",
    "post_run_finding",
    "run_report",
]
