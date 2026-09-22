"""Transcription-cog wrappers around :mod:`mini_app_polis.pipeline_status`.

This module is a thin shim around the shared pipeline-status helpers in
**common-python-utils**. Every Kaiano cog self-reports the outcome of
its runs the same way, and the actual machinery (``post_run_finding``,
``run_report``, :class:`RunReport`) lives in the shared library so the payload shape and best-effort semantics stay
in sync across cogs.

The shim's only job is to pre-bind ``repo="transcription-cog"`` on
``post_run_finding`` and ``run_report`` so call sites don't have to
repeat it.

The Prefect pieces of the library — ``make_failure_hook``,
``get_prefect_logger`` and ``get_run_id`` — are not re-exported. This cog
runs on Lambda: a run's id is the queue message id, passed in by the
worker, and a run that raises is reported by ``run_report`` on its way
out rather than by a Prefect state hook.

The voicenotes flow inside this package has its own per-row translation
needs, so it builds a :class:`RunReport` directly rather than going
through this shim.
"""

from __future__ import annotations

from contextlib import AbstractContextManager
from typing import Any

from mini_app_polis.pipeline_status import (
    DeliveryReport,
    RunReport,
    Severity,
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
    run_id: str | None = None,
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
        run_id=run_id,
        **extras,
    )


def run_report(
    flow_name: str,
    *,
    production_only: bool = True,
    notable: bool = False,
    source: str = "flow_inline",
    run_id: str | None = None,
) -> AbstractContextManager[RunReport]:
    """Open a run report for this cog, with ``repo`` pre-bound.

    Identical to :func:`mini_app_polis.pipeline_status.run_report` except
    that ``repo`` is fixed to ``"transcription-cog"``. Pass ``run_id``:
    the library's fallback only knows Prefect's ids.
    """
    return _run_report(
        flow_name,
        repo=REPO,
        production_only=production_only,
        notable=notable,
        source=source,
        run_id=run_id,
    )


__all__ = [
    "REPO",
    "RunReport",
    "Severity",
    "post_run_finding",
    "run_report",
]
