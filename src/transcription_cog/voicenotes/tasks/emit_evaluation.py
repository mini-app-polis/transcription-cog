"""Prefect task: report what the voicenotes batch did.

What a voice-note batch did is run status, not a finding. A finding is
something an evaluator graded against a revision of the standards
catalog; "three files failed to transcribe" was never graded against
anything, and writing it to the evaluations table is what made those
rows need a null ``standards_version`` to stop the dashboard claiming
otherwise. So this reports to ``POST /v1/notify`` and writes no rows.

**One message per run, not one per file.** A bad batch is usually one
event wearing N hats — the worker died, the API is down, the folder
filled with something unreadable — and nine notifications describing it
nine times is nine interruptions for the same news. The per-file detail
is kept, as lines inside the one message.

This task is a **thin cog-specific adapter** on top of
:class:`mini_app_polis.pipeline_status.RunReport`. The library owns
auth, delivery, the processor-version stamp, the message layout and the
best-effort semantics. This file's only job is folding the cog's
batch-shape input into that object's vocabulary.

It used to compose the message itself, and that is how a processed voice
note came to announce ``1 file(s) seen`` and nothing about the Asana task
it had just created. Composing here meant the cog could only say what it
had thought to say; the run's *outcomes* — the tasks created, the
recordings the retention sweep removed — had no shape to go in. The
sweep eventually got one, as a local ``notices`` parameter invented for
that single case. Both are now ordinary library outcomes, and the next
thing this pipeline starts doing will be too.

Failure semantics: delivery failures are logged and reported to Sentry
by the library, never raised. Notification is observability, not the
source of truth — a flaky api-kaianolevine-com must not turn a
successful voice-note ingest into a failure.

**Severity.** Any flagged file or failed sweep makes the run WARN. There
is no ERROR here on purpose: ERROR means the flow itself died, which is
a Prefect state this task does not have and must not guess at. The
``on_failure`` / ``on_crashed`` hook in :mod:`..flows.ingest` reports
that case with the state that caused it. A batch that finished with two
of five files failed is a WARN — a completed run with results worth a
human look, which is exactly what the library documents WARN to mean.
"""

from __future__ import annotations

from typing import Any

from mini_app_polis.pipeline_status import CREATED, REMOVED, UPDATED, RunReport
from prefect import task

from transcription_cog._pipeline_eval import REPO
from transcription_cog.voicenotes._shared import get_logger
from transcription_cog.voicenotes.config import settings

_logger = get_logger("voicenotes-cog")


# Flow name written into every run report. Matches the ``@flow``
# decorator name in flows/ingest.py — keep these in sync.
#
# Both pipelines in this repo report under the same ``transcription-cog``
# repo label since the May-2026 merge from the old ``notes-ingest-cog``
# and ``voicenotes-cog`` standalone repos (ADR-004). They stay
# distinguishable in Pipeline Health via ``flow_name`` —
# ``process-transcript`` for WCS transcripts, ``voicenotes-ingest`` for
# voicenotes — not via a per-pipeline repo facet.
_FLOW_NAME = "voicenotes-ingest"

# Canonical ``source`` values per ecosystem-standards
# ``standards/evaluation.yaml``. The Pipeline Health UI derives its "Run
# Type" facet from this field — rows with non-canonical source values are
# silently hidden from every Run Type bucket. Per-file context lives in
# the message text; ``source`` is reserved for the run-type marker.
_SOURCE_FLOW_INLINE = "flow_inline"

# Recorded when a finding names no step. Findings from the per-file loop
# always carry one; a malformed caller should still land somewhere named
# rather than under an empty reason.
_UNATTRIBUTED = "failed"

_OPS = {CREATED, UPDATED, REMOVED}


def build_report(
    *,
    files_seen: int = 0,
    files_processed: int = 0,
    findings: list[dict[str, Any]] | None = None,
    outcomes: list[dict[str, Any]] | None = None,
    duration_sec: float | None = None,
) -> RunReport:
    """Fold one batch outcome into a :class:`RunReport`.

    Separate from :func:`emit_evaluation` so the message this run would
    send can be asserted without a Prefect task run or a delivery stub.

    ``files_seen`` is carried as a counter rather than the headline
    because it is not the same number as the work done, and the gap
    between them is the interesting part: a scan that saw four files and
    processed one has three lines saying why, and a scan that saw files
    and processed none looks identical to success in every other counter.

    Findings are grouped by the step that produced them, so a batch where
    the same thing broke five times reads as one line with five names
    rather than five lines.
    """
    report = RunReport(
        flow_name=_FLOW_NAME,
        repo=REPO,
        duration_sec=duration_sec,
    )
    report.ok(files_processed)
    report.count("seen", files_seen)

    for outcome in outcomes or []:
        op = str(outcome.get("op") or CREATED)
        if op not in _OPS:
            # An unknown op would silently vanish from the rendered
            # message, since the library walks a fixed list of three.
            # Better to announce it under the one that is always true of
            # a voicenotes outcome than to lose it.
            op = CREATED
        record = {
            CREATED: report.created,
            UPDATED: report.updated,
            REMOVED: report.removed,
        }[op]
        record(
            str(outcome.get("kind") or "item"),
            outcome.get("item"),
            link=outcome.get("link"),
        )

    for problem in findings or []:
        reason = str(problem.get("failed_at_task") or _UNATTRIBUTED)
        item = problem.get("name") or problem.get("drive_file_id")
        report.issue(
            reason,
            str(item) if item else None,
            detail=str(problem.get("message") or problem.get("finding") or "no detail"),
        )

    return report


@task(
    name="emit_evaluation",
    retries=settings.extract_task_retries,
    retry_delay_seconds=settings.extract_task_retry_delays_seconds,
)
def emit_evaluation(
    flow_run_id: str,
    files_seen: int = 0,
    files_processed: int = 0,
    findings: list[dict[str, Any]] | None = None,
    outcomes: list[dict[str, Any]] | None = None,
    duration_sec: float | None = None,
    source: str = _SOURCE_FLOW_INLINE,
) -> None:
    """Report this batch's outcome as one notification.

    Args:
        flow_run_id: Prefect flow run identifier. The library resolves its
            own run id from the Prefect runtime; this is kept on the
            signature for existing call sites and logged locally.
        files_seen: How many audio files the scan found.
        files_processed: How many came through the pipeline cleanly.
        findings: Per-file failure dicts — ``failed_at_task``, ``name`` or
            ``drive_file_id``, and ``message``. Each becomes an issue,
            grouped by step.
        outcomes: What this run made exist or removed, as
            ``{"op", "kind", "item", "link"}`` dicts. An Asana task
            created, a recording the retention sweep trashed.
        duration_sec: How long the batch took. Supplied rather than
            measured here because this task runs at the end of the flow,
            long after the clock started.
        source: ``"flow_inline"`` for the end-of-flow emission.

    A run that recorded any outcome is notable by that fact, so a batch
    that created a task reaches the channel without the flow having to
    say it is worth sending. A triggered scan that found nothing is still
    marked notable here: the watcher fired for a reason, and "it fired
    and there was nothing there" is the mismatch worth surfacing rather
    than the one worth hiding.
    """
    report = build_report(
        files_seen=files_seen,
        files_processed=files_processed,
        findings=findings,
        outcomes=outcomes,
        duration_sec=duration_sec,
    )

    _logger.info(
        "voicenotes.emit_evaluation.start",
        category="api",
        context={
            "flow_run_id": flow_run_id,
            "files_seen": files_seen,
            "files_processed": files_processed,
            "outcome_count": len(outcomes or []),
            "failure_count": len(findings or []),
        },
    )

    # The library is best-effort and does not raise, but this task runs
    # inside the flow's housekeeping. Reporting on a batch must never be
    # the reason the batch is recorded as failed, so the guarantee is
    # enforced here rather than assumed.
    try:
        result = report.send(notable=True, source=source)
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
