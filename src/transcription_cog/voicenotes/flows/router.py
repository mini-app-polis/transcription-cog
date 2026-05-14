"""Single-deployment router flow.

Prefect Cloud's free tier caps the workspace at 5 deployments, so
voicenotes-cog registers exactly one deployment. This router flow
sits behind it and dispatches to either ``voicenotes_ingest`` or
``voicenotes_cleanup`` based on a ``mode`` parameter.

Triggers:

  - **Ad-hoc** from watcher-cog whenever a new/modified file is
    detected anywhere in the watched ``voice-inbox/`` folder. Watcher
    fires the deployment with ``parameters={"mode": "ingest"}`` —
    no per-file ID is passed; the ingest flow scans the inbox,
    processes whatever it finds, and runs cleanup at the end.

  - **Manual** with ``parameters={"mode": "cleanup"}`` — kept
    reachable so an operator can force an out-of-band sweep from
    the Prefect UI. Normal cleanup runs are folded into the end
    of every ingest cycle, so this manual mode is rarely needed.

Mirrors evaluator-cog's single-parameterized-deployment pattern.
"""

from __future__ import annotations

import logging
from typing import Any

from prefect import flow, get_run_logger

from transcription_cog.voicenotes._shared import get_logger
from transcription_cog.voicenotes.flows.cleanup import voicenotes_cleanup
from transcription_cog.voicenotes.flows.ingest import voicenotes_ingest

_logger = get_logger("voicenotes-cog")

# Allowed values for the ``mode`` parameter. Surfaced as constants so
# callers (watcher-cog, manual operator triggers, smoke tests) can refer
# to a single source of truth.
MODE_INGEST = "ingest"
MODE_CLEANUP = "cleanup"
_VALID_MODES = (MODE_INGEST, MODE_CLEANUP)


@flow(name="voicenotes-router")
def voicenotes_router(mode: str = MODE_INGEST) -> dict[str, Any]:
    """Dispatch to the ingest or cleanup flow based on ``mode``.

    Parameters
    ----------
    mode
        Either ``"ingest"`` or ``"cleanup"``. Defaults to ``"ingest"``
        — i.e., an ad-hoc trigger that doesn't specify a mode runs
        ingest, which is the safer default (it scans the inbox and
        processes new files; running this redundantly is idempotent
        thanks to the post_task drive_file_id marker).

    Returns
    -------
    The summary dict returned by the dispatched flow. Shape differs
    between modes — see each flow's docstring.

    Raises
    ------
    ValueError
        If ``mode`` is not one of the supported values.
    """
    flow_logger = _flow_logger()
    flow_logger.info(f"voicenotes.router.start mode={mode!r}")
    _logger.info(
        "voicenotes.router.start",
        category="pipeline",
        context={"mode": mode},
    )

    if mode not in _VALID_MODES:
        raise ValueError(
            f"voicenotes-router: unknown mode {mode!r}; "
            f"expected one of {_VALID_MODES!r}"
        )

    if mode == MODE_INGEST:
        return voicenotes_ingest()

    # mode == MODE_CLEANUP
    return voicenotes_cleanup()


def _flow_logger():
    """PIPE-006 dual-logger: Prefect run logger inside flow, fallback outside."""
    try:
        return get_run_logger()
    except Exception:
        return logging.getLogger(__name__)
