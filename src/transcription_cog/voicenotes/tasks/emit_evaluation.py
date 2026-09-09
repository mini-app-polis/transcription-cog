"""Prefect task: report the voicenotes batch outcome.

What a voice-note batch did is run status, not a finding. A finding is
something an evaluator graded against a revision of the standards
catalog; "three files failed to transcribe" was never graded against
anything, and writing it to the evaluations table is what made those
rows need a null ``standards_version`` to stop the dashboard claiming
otherwise. So this reports to ``POST /v1/notify`` and writes no rows.

**One message per run, not one per failed file.** A bad batch is
usually one event wearing N hats — the worker died, the API is down,
the folder filled with something unreadable — and nine notifications
describing it nine times is nine interruptions for the same news. The
per-file detail is kept, as lines inside the one message.

This task is a **thin cog-specific adapter** on top of
:func:`transcription_cog._pipeline_eval.post_run_finding`. The shared
library owns auth, delivery, the processor-version stamp and the
best-effort semantics. This file's only job is folding the cog's
batch-shape input (a list of dicts with ``category``, ``severity``,
``message``, ``drive_file_id``, ``failed_at_task``) into one outcome.

Failure semantics: delivery failures are logged and reported to Sentry
by the library, never raised. Notification is observability, not the
source of truth — a flaky api-kaianolevine-com must not turn a
successful voice-note ingest into a failure.
"""

from __future__ import annotations

from typing import Any

from mini_app_polis.pipeline_status import Severity
from prefect import task

from transcription_cog._pipeline_eval import post_run_finding
from transcription_cog.voicenotes._shared import get_logger
from transcription_cog.voicenotes.config import settings

_logger = get_logger("voicenotes-cog")


# Repo identifier written into every finding row. Single source of
# truth so it can't drift between findings.
#
# Both pipelines in this repo (WCS transcripts via flow.py and
# voicenotes via voicenotes/flows/) self-report under the same
# ``transcription-cog`` repo label since the May-2026 merge from the
# old ``notes-ingest-cog`` and ``voicenotes-cog`` standalone repos
# (docs/decisions/ADR-004-voicenotes-merge.md). The two pipelines stay
# distinguishable in the Pipeline Health UI via ``flow_name`` —
# ``process-transcript`` for WCS transcripts, ``voicenotes-ingest``
# for voicenotes — not via a per-pipeline repo facet.
_REPO_NAME = "transcription-cog"

# Flow name written into every finding row. Matches the ``@flow``
# decorator name in flows/ingest.py — keep these in sync.
_FLOW_NAME = "voicenotes-ingest"

# Default dimension for runtime ingest findings. ecosystem-standards
# defines several dimensions (structural_conformance,
# pipeline_consistency, testing_coverage, ...); ``pipeline_consistency``
# is the closest fit for runtime flow execution health.
_DIMENSION_PIPELINE = "pipeline_consistency"

# Canonical ``source`` values per ecosystem-standards
# ``standards/evaluation.yaml``. The Pipeline Health UI derives its
# "Run Type" facet from this field — rows with non-canonical source
# values are silently hidden from every Run Type bucket (only visible
# under "All"). The previous practice of stuffing drive_file_id into
# ``source`` had exactly that symptom. Per-file context now lives in
# the ``finding`` text; ``source`` is reserved for the canonical
# run-type marker.
_SOURCE_FLOW_INLINE = "flow_inline"  # end-of-flow emissions
_SOURCE_FLOW_HOOK = "flow_hook"  # on_failure / on_crashed emissions


def _append_drive_file_id(text: str, drive_file_id: str | None) -> str:
    """Append ``(drive_file_id=...)`` to a finding text when meaningful.

    The literal ``"batch"`` is a sentinel used by the inline body and
    failure hook for the aggregate row; appending it adds noise without
    information, so it's elided.
    """
    if not drive_file_id or drive_file_id == "batch":
        return text
    return f"{text} (drive_file_id={drive_file_id})"


#: Cap on how many per-file failures are listed in the message body.
#: Past this the list stops being read and starts being scrolled; the
#: count still says how many there were, and the log has all of them.
_MAX_LISTED_FAILURES = 10


def _summarise(
    *,
    drive_file_id: str,
    success: bool,
    findings: list[dict[str, Any]] | None,
    files_seen: int | None,
    notices: list[str] | None = None,
    files_failed: int | None = None,
) -> tuple[Severity, str]:
    """Fold one batch outcome into a single severity and message body.

    Severity is the batch's, not the worst row's: a batch that finished
    with some files failed is a WARN, and a batch that died is an ERROR
    however few files it had got through.

    ``notices`` are lines that are worth reporting but are not problems
    — today, that the retention sweep permanently deleted archived
    audio. They never affect severity: a clean run that also deleted
    thirty expired recordings is still a SUCCESS. They exist because a
    permanent delete was previously the one thing this cog did that
    produced no notification at all, only a Railway log line.

    ``files_failed`` is the headline's counter and is not the same as
    ``len(findings)``. Not every finding is a file: a failed retention
    sweep is one finding and zero failed files, and counting it in the
    headline produced "1 file(s) seen, 1 failed" for a run whose file
    processed perfectly. When it is not supplied the finding count is
    used, which is correct for callers whose findings are all per-file.
    """
    problems = list(findings or [])

    if not success:
        severity: Severity = "ERROR"
    elif problems:
        severity = "WARN"
    else:
        severity = "SUCCESS"

    if not success and not problems:
        headline = "voicenotes ingest terminal failure"
    elif files_seen is None:
        headline = "voicenotes ingest completed"
    else:
        headline = f"voicenotes ingest: {files_seen} file(s) seen"
        failed_count = len(problems) if files_failed is None else files_failed
        if failed_count:
            headline += f", {failed_count} failed"

    lines = [_append_drive_file_id(headline, drive_file_id)]
    for problem in problems[:_MAX_LISTED_FAILURES]:
        message = str(problem.get("message") or problem.get("finding") or "no message")
        line = _append_drive_file_id(message, str(problem.get("drive_file_id") or ""))
        failed_at = problem.get("failed_at_task")
        if failed_at:
            line += f" [failed at {failed_at}]"
        lines.append(f"• {line}")

    remaining = len(problems) - _MAX_LISTED_FAILURES
    if remaining > 0:
        lines.append(f"• …and {remaining} more")

    for notice in notices or []:
        lines.append(f"• {notice}")

    return severity, "\n".join(lines)


@task(
    name="emit_evaluation",
    retries=settings.extract_task_retries,
    retry_delay_seconds=settings.extract_task_retry_delays_seconds,
)
def emit_evaluation(
    flow_run_id: str,
    drive_file_id: str,
    success: bool,
    findings: list[dict[str, Any]] | None = None,
    source: str = _SOURCE_FLOW_INLINE,
    files_seen: int | None = None,
    notices: list[str] | None = None,
    files_failed: int | None = None,
) -> None:
    """Report this batch's outcome as one notification.

    Args:
        flow_run_id: Prefect flow run identifier. The library resolves
            its own run id from the Prefect runtime; this is kept on the
            signature for existing call sites and logged locally.
        drive_file_id: The voice-note source file id, or ``"batch"`` for
            the aggregate emission.
        success: Aggregate batch success.
        findings: Optional list of cog-shaped per-file failure dicts.
            Each becomes one line inside the single message.
        source: ``"flow_inline"`` for the end-of-flow emission,
            ``"flow_hook"`` from the on_failure / on_crashed hooks.
        files_seen: How many audio files the scan found. Reported in the
            message text; it no longer gates whether the message is sent.
            It briefly did, on the theory that an empty scan was an idle
            tick — but an ad-hoc scan that found nothing means the
            watcher fired and the files were already gone, which is the
            mismatch worth surfacing rather than hiding.
        notices: Non-problem lines to include in the message — currently
            what the retention sweep deleted. Never affects severity.
        files_failed: How many audio files failed, for the headline.
            Distinct from the finding count, which can include
            non-file problems such as a failed retention sweep.
    """
    severity, text = _summarise(
        drive_file_id=drive_file_id,
        success=success,
        findings=findings,
        files_seen=files_seen,
        notices=notices,
        files_failed=files_failed,
    )

    _logger.info(
        "voicenotes.emit_evaluation.start",
        category="api",
        context={
            "flow_run_id": flow_run_id,
            "success": success,
            "files_seen": files_seen,
            "failure_count": len(findings or []),
        },
    )

    # The library is best-effort and does not raise, but this task runs
    # inside the flow's housekeeping and from its crash hooks. Reporting
    # on a batch must never be the reason the batch is recorded as
    # failed, so the guarantee is enforced here too rather than assumed.
    try:
        result = post_run_finding(
            _FLOW_NAME,
            severity,
            text,
            source=source,
            # Unconditional, because this deployment has no cron either —
            # it runs when watcher-cog fires it. A cycle that scanned an
            # empty inbox did not idle; it was triggered and found
            # nothing, which is the case most worth hearing about.
            notable=True,
        )
    except Exception as exc:  # noqa: BLE001 - see comment above
        _logger.error(
            "voicenotes.emit_evaluation.failed",
            category="api",
            context={"flow_run_id": flow_run_id, "error": repr(exc)},
        )
        return

    _logger.info(
        "voicenotes.emit_evaluation.done",
        category="api",
        context={
            "flow_run_id": flow_run_id,
            "sent": result.sent,
            "suppressed": result.suppressed,
            "failed": result.failed,
        },
    )
