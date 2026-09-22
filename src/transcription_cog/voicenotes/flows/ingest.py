"""Voice notes: one audio file from ``voice-inbox/``, end to end.

One queue message is one file. watcher-cog names each file that changes
in ``voice-inbox/``, the API enqueues one job per file, and the Lambda
worker calls :func:`voicenotes_ingest` with its id. This used to be a
Prefect flow that swept the whole inbox; a sweep of several notes could
outlive Lambda's 900-second ceiling, and one note cannot. See
docs/decisions/ADR-007-lambda-behind-sqs.md.

Per-file order matters:

  1. download
  2. transcribe
  3. extract
  4. post_task         ← only commit point that creates external state
  5. archive           ← MUST run after post_task; before would risk losing audio

If any step before ``post_task`` fails, the file stays in
``voice-inbox/`` and the flow raises, so the worker hands the message
back and the queue redelivers it.

If ``post_task`` succeeds but ``archive`` fails, the redelivered job
sees the file again. The post_task idempotency check
(``external.gid = voicenote.<drive_file_id>`` on the Asana task)
prevents duplicate tasks, including for notes already completed.

A file that is no longer in the inbox was archived by an earlier job for
the same file — watcher asks again after a partial failure or a restart.
That run does nothing and says so quietly.

The retention sweep runs at the end of every run, best-effort, and one
report per run carries both.
"""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any

from mini_app_polis import logger as log
from mini_app_polis.pipeline_status import CREATED, REMOVED

from transcription_cog._deadline import RunOutOfTime
from transcription_cog.voicenotes._shared import get_logger
from transcription_cog.voicenotes.clients.drive_client import get_drive_client
from transcription_cog.voicenotes.config import require_voicenotes_settings, settings
from transcription_cog.voicenotes.flows.cleanup import voicenotes_cleanup
from transcription_cog.voicenotes.tasks.archive import archive_audio
from transcription_cog.voicenotes.tasks.download import download_audio
from transcription_cog.voicenotes.tasks.emit_evaluation import emit_evaluation
from transcription_cog.voicenotes.tasks.extract import extract
from transcription_cog.voicenotes.tasks.post_task import post_task
from transcription_cog.voicenotes.tasks.transcribe import transcribe

_logger = get_logger("voicenotes-cog")
# The shared logger rather than a stdlib one: Lambda's runtime sets the
# root logger to WARNING, and the shared logger is what gets past that.
_log = log.get_logger()

# Drive mime type for folders. ``processed/`` lives inside the inbox, and
# a folder is never a voice note.
_DRIVE_FOLDER_MIME = "application/vnd.google-apps.folder"

#: The flow name the standalone retention sweep reports under.
_CLEANUP_FLOW_NAME = "voicenotes-cleanup"


# ---------------------------------------------------------------------------
# Per-file pipeline
# ---------------------------------------------------------------------------


def _process_one_file(drive_file_id: str) -> dict[str, Any]:
    """Run the full per-file pipeline. Returns a result dict on success.

    Raises whatever the underlying steps raise.

    The task's title, url and whether it is new are carried back out
    alongside the gid. They were not, and the batch report could
    therefore only ever say how many files it had seen — the one fact
    about a processed voice note that does not answer "and then what?".
    """
    audio_bytes = download_audio(drive_file_id)
    transcription = transcribe(audio_bytes, drive_file_id=drive_file_id)
    extracted = extract(transcription.text, drive_file_id=drive_file_id)
    posted = post_task(extracted, drive_file_id=drive_file_id)
    archive_audio(drive_file_id)
    return {
        "drive_file_id": drive_file_id,
        "asana_task_id": posted.gid,
        "asana_task_created": posted.created,
        "asana_task_title": (extracted.title or "").strip() or posted.gid,
        "asana_task_url": posted.url,
        "needs_review": extracted.needs_review,
        "transcription_cost_usd": float(
            getattr(transcription, "cost_usd_estimate", None) or 0.0
        ),
    }


def _failed_step_from_exception(exc: BaseException) -> str:
    """Walk the traceback to identify which step raised. ``unknown`` if unclear."""
    markers = (
        ("download", "download_audio"),
        ("transcribe", "transcribe"),
        ("extract", "extract"),
        ("post_task", "post_task"),
        ("archive", "archive_audio"),
    )
    tb = exc.__traceback__
    while tb is not None:
        filename = tb.tb_frame.f_code.co_filename
        for marker, task_name in markers:
            if f"/tasks/{marker}.py" in filename:
                return task_name
        tb = tb.tb_next
    return "unknown"


def _find_in_inbox(drive_file_id: str) -> Any | None:
    """The inbox entry for this file, or None if it is not there.

    Listed rather than fetched by id, because "is it still in the inbox"
    is the question: a file that has left it was archived by an earlier
    job for the same file.
    """
    drive = get_drive_client()
    for entry in drive.list_files(settings.google_drive_voice_inbox_folder_id):
        if getattr(entry, "id", None) != drive_file_id:
            continue
        if getattr(entry, "mime_type", None) == _DRIVE_FOLDER_MIME:
            return None
        return entry
    return None


# ---------------------------------------------------------------------------
# Retention sweep, as report lines
# ---------------------------------------------------------------------------


def _run_cleanup() -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    """Run the retention sweep and return ``(findings, outcomes)`` for the report.

    Never raises. Cleanup is best-effort housekeeping and must not fail an
    otherwise-successful ingest; a sweep that failed is reported as a
    finding at its own severity instead, so "off the success path" does
    not mean "invisible".
    """
    findings: list[dict[str, Any]] = []
    outcomes: list[dict[str, Any]] = []
    try:
        summary = voicenotes_cleanup()
        failed = int(summary.get("failed", 0) or 0)
        trashed = int(summary.get("trashed", 0) or 0)
        _log.info(
            f"voicenotes.flow.cleanup_completed trashed={trashed} failed={failed}"
        )
        buckets = int(summary.get("buckets_trashed", 0) or 0)
        if trashed:
            # Removing the operator's audio is an outcome, the same kind of
            # thing as creating a task, and rides in the run's one message.
            cutoff = summary.get("cutoff_date") or "the retention window"
            outcomes.append(
                {
                    "op": REMOVED,
                    "kind": "recording",
                    "item": (
                        f"{trashed} archived before {cutoff} "
                        "(in shared drive trash for 30 days)"
                    ),
                }
            )
            if buckets:
                outcomes.append(
                    {
                        "op": REMOVED,
                        "kind": "date folder",
                        "item": f"{buckets} emptied",
                    }
                )
        if failed:
            findings.append(
                {
                    "category": "pipeline",
                    # Every deletion failing is a sweep that does not
                    # work; some failing is a degraded one.
                    "severity": "ERROR" if trashed == 0 else "WARN",
                    "message": (
                        f"retention sweep trashed {trashed} of "
                        f"{summary.get('attempted', failed)} eligible files: "
                        f"{summary.get('first_error') or 'see logs'}"
                    ),
                    "drive_file_id": "cleanup",
                    "failed_at_task": "cleanup",
                }
            )
    except Exception as exc:  # noqa: BLE001 — see docstring
        _log.warning(f"voicenotes.flow.cleanup_failed error={exc!r}")
        _logger.warning(
            "voicenotes.flow.cleanup_failed",
            category="pipeline",
            context={"error": str(exc), "error_type": type(exc).__name__},
        )
        findings.append(
            {
                "category": "pipeline",
                "severity": "ERROR",
                "message": f"retention sweep raised: {exc}",
                "drive_file_id": "cleanup",
                "failed_at_task": "cleanup",
            }
        )
    return findings, outcomes


# ---------------------------------------------------------------------------
# Entry points
# ---------------------------------------------------------------------------


def voicenotes_ingest(
    drive_file_id: str, *, run_id: str | None = None
) -> dict[str, Any]:
    """Process one voice note from ``voice-inbox/``, then sweep the archive.

    ``run_id`` is the queue message id when the Lambda worker runs this.

    Returns
    -------
    Summary dict::

        {
            "files_processed": int,
            "files_failed": int,
            "files_seen": int,
            "duration_sec": float,
            "total_cost_usd_estimate": float,
            "run_id": str | None,
            "results": [<per-file success dicts>],
            "failures": [<per-file failure dicts>],
        }

    Raises when the note could not be processed, after its one report is
    sent, so the worker returns the message to the queue. The report is
    this flow's to send — the worker does not send a second.
    """
    started_at = datetime.now(UTC)
    _log.info(f"voicenotes.flow.start drive_file_id={drive_file_id}")
    _logger.info(
        "voicenotes.flow.start",
        category="pipeline",
        context={"run_id": run_id, "drive_file_id": drive_file_id},
    )

    results: list[dict[str, Any]] = []
    failures: list[dict[str, Any]] = []
    files_seen = 0
    gone = False
    # What the run died of, if it did. Held until the report is sent.
    raised: BaseException | None = None

    try:
        # Fail fast and loud if voicenotes config is missing. Module
        # import accepts empty defaults — see voicenotes.config.
        require_voicenotes_settings()
        entry = _find_in_inbox(drive_file_id)
        if entry is None:
            gone = True
            _log.info(
                f"voicenotes.flow.not_in_inbox drive_file_id={drive_file_id} — "
                "already archived by an earlier job"
            )
        else:
            files_seen = 1
            try:
                results.append(_process_one_file(drive_file_id))
            except Exception as exc:
                failed_at = _failed_step_from_exception(exc)
                failures.append(
                    {
                        "drive_file_id": drive_file_id,
                        "name": getattr(entry, "name", None),
                        "failed_at_task": failed_at,
                        "error": str(exc),
                        "error_type": type(exc).__name__,
                    }
                )
                _log.error(
                    f"voicenotes.flow.file_failure drive_file_id={drive_file_id} "
                    f"failed_at={failed_at} error={exc!r}"
                )
                _logger.error(
                    "voicenotes.flow.file_failure",
                    category="pipeline",
                    context={
                        "drive_file_id": drive_file_id,
                        "failed_at_task": failed_at,
                        "error": str(exc),
                        "error_type": type(exc).__name__,
                        "run_id": run_id,
                    },
                )
                raised = exc
    except BaseException as exc:
        # Configuration or the inbox listing: nothing about the note
        # itself, but still this run's failure to report.
        failures.append(
            {
                "drive_file_id": drive_file_id,
                "name": None,
                "failed_at_task": "setup",
                "error": str(exc),
                "error_type": type(exc).__name__,
            }
        )
        raised = exc

    # The sweep runs after a note that failed as well as one that
    # succeeded — its failure is the note's, not the archive's. It does
    # not run when the run could not even start, nor when it is out of
    # time: what is left of the margin belongs to the report.
    swept = (raised is None or files_seen) and not isinstance(raised, RunOutOfTime)
    cleanup_findings, outcomes_from_cleanup = _run_cleanup() if swept else ([], [])

    findings: list[dict[str, Any]] = [
        {
            "category": "pipeline",
            "severity": "ERROR",
            "message": fail["error"],
            "drive_file_id": fail["drive_file_id"],
            "name": fail.get("name"),
            "failed_at_task": fail["failed_at_task"],
        }
        for fail in failures
    ]
    findings.extend(cleanup_findings)

    # What this run made exist, which is what a person opening the
    # channel actually wants from a voice note. A replayed file whose
    # task was already there created nothing and says so by absence —
    # the idempotency guard working is not news.
    outcomes: list[dict[str, Any]] = [
        {
            "op": CREATED,
            "kind": "asana task",
            "item": r["asana_task_title"],
            "link": r["asana_task_url"],
        }
        for r in results
        if r.get("asana_task_created")
    ]
    outcomes.extend(outcomes_from_cleanup)

    duration_sec = (datetime.now(UTC) - started_at).total_seconds()
    emit_evaluation(
        run_id=run_id,
        files_seen=files_seen,
        files_processed=len(results),
        findings=findings,
        outcomes=outcomes,
        duration_sec=duration_sec,
        # A file an earlier job already finished is the guard working.
        # Anything else was asked for by name, and says what happened.
        notable=not (gone and not findings and not outcomes),
    )

    if raised is not None:
        raise raised

    total_cost_usd = sum(r.get("transcription_cost_usd", 0.0) for r in results)
    _logger.info(
        "voicenotes.flow.success",
        category="pipeline",
        context={
            "files_processed": len(results),
            "files_seen": files_seen,
            "duration_sec": duration_sec,
            "total_cost_usd_estimate": total_cost_usd,
            "run_id": run_id,
        },
    )
    return {
        "files_processed": len(results),
        "files_failed": len(failures),
        "files_seen": files_seen,
        "duration_sec": duration_sec,
        "total_cost_usd_estimate": total_cost_usd,
        "run_id": run_id,
        "results": results,
        "failures": failures,
    }


def voicenotes_cleanup_run(*, run_id: str | None = None) -> dict[str, Any]:
    """The retention sweep on its own, for an operator who asks for one.

    Ingest runs the sweep after every note. This is the
    ``voicenotes-cleanup`` mode: the same sweep, reported as its own run —
    which the Prefect router's cleanup mode never did.
    """
    started_at = datetime.now(UTC)
    raised: BaseException | None = None
    try:
        require_voicenotes_settings()
        findings, outcomes = _run_cleanup()
    except BaseException as exc:
        raised = exc
        outcomes = []
        findings = [
            {
                "category": "pipeline",
                "severity": "ERROR",
                "message": str(exc),
                "drive_file_id": "cleanup",
                "failed_at_task": "setup",
            }
        ]
    emit_evaluation(
        run_id=run_id,
        findings=findings,
        outcomes=outcomes,
        duration_sec=(datetime.now(UTC) - started_at).total_seconds(),
        flow_name=_CLEANUP_FLOW_NAME,
    )
    if raised is not None:
        raise raised
    return {"run_id": run_id, "findings": findings, "outcomes": outcomes}
