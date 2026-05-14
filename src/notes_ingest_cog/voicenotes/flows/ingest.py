"""Main ingest flow: scan ``voice-inbox/`` and process each new audio file.

Triggered by watcher-cog when *any* file change is detected in the
watched folder. Watcher fires a single generic trigger per polling
cycle and does not pass file IDs, so this flow scans
``voice-inbox/`` itself, processes every file at the root (skipping
subfolders like ``processed/``), and continues across per-file
failures.

Mirrors the notes-ingest-cog pattern.

Per-file order matters:

  1. download
  2. transcribe
  3. extract
  4. post_task         ← only commit point that creates external state
  5. archive           ← MUST run after post_task; before would risk losing audio

If any step before ``post_task`` fails for a file: that file stays
in ``voice-inbox/``, watcher will re-trigger on the next cycle.

If ``post_task`` succeeds but ``archive`` fails: the next watcher
cycle will see the file again. The post_task idempotency check
(drive_file_id marker embedded in the Todoist task description)
prevents duplicate Todoist tasks.

Per-batch ``emit_evaluation`` runs once at the end with aggregated
results — Whisper/Claude cost is summed across files, and per-file
failures are surfaced as findings.
"""

from __future__ import annotations

import logging
from datetime import UTC, datetime
from typing import Any

from prefect import flow, get_run_logger
from prefect.concurrency.sync import concurrency
from prefect.context import get_run_context

from notes_ingest_cog.voicenotes._shared import get_logger
from notes_ingest_cog.voicenotes.clients.drive_client import get_drive_client
from notes_ingest_cog.voicenotes.config import require_voicenotes_settings, settings
from notes_ingest_cog.voicenotes.flows.cleanup import voicenotes_cleanup
from notes_ingest_cog.voicenotes.tasks.archive import archive_audio
from notes_ingest_cog.voicenotes.tasks.download import download_audio
from notes_ingest_cog.voicenotes.tasks.emit_evaluation import emit_evaluation
from notes_ingest_cog.voicenotes.tasks.extract import extract
from notes_ingest_cog.voicenotes.tasks.post_task import post_task
from notes_ingest_cog.voicenotes.tasks.transcribe import transcribe

_logger = get_logger("voicenotes-cog")
_log = logging.getLogger(__name__)

# Named Prefect concurrency slot — must exist in Prefect Cloud
# (Settings → Concurrency, limit=1) before the cog is deployed.
# Per ecosystem-standards PIPE-009: prevents two concurrent runs
# from scanning the same Drive folder state simultaneously and
# double-processing the same files.
_CONCURRENCY_SLOT = "voicenotes-cog"

# Drive mime type for folders. Used to skip the ``processed/``
# subfolder (and any others) during inbox scanning.
_DRIVE_FOLDER_MIME = "application/vnd.google-apps.folder"


def _flow_logger():
    """PIPE-006 dual-logger: Prefect run logger inside flow, stdlib outside."""
    try:
        return get_run_logger()
    except Exception:
        return _log


def _current_flow_run_id() -> str:
    """Best-effort fetch of the current Prefect flow run id."""
    try:
        ctx = get_run_context()
        flow_run = getattr(ctx, "flow_run", None)
        if flow_run is not None and getattr(flow_run, "id", None):
            return str(flow_run.id)
    except Exception:
        pass
    return f"local-{datetime.now(UTC).isoformat()}"


def _emit_terminal_failure(flow, flow_run, state) -> None:
    """Prefect flow hook: emit a final pipeline_evaluation on crash.

    Used as ``on_crashed`` and ``on_failure``. Covers the scenario
    where the flow body never reaches its inline ``except`` branches
    — e.g., worker SIGKILL, OOM, or an exception escaping the
    ``with concurrency()`` block. Best-effort; never re-raises.
    """
    flow_run_id = (
        str(flow_run.id) if getattr(flow_run, "id", None) else _current_flow_run_id()
    )
    message = str(getattr(state, "message", "") or type(state).__name__)
    try:
        # source="flow_hook" tells the Pipeline Health UI's Run Type
        # facet that this row came from an on_failure / on_crashed
        # hook rather than the inline body — load-bearing for
        # distinguishing "flow caught an error" from "flow died".
        emit_evaluation.fn(
            flow_run_id=flow_run_id,
            drive_file_id="batch",
            success=False,
            findings=[
                {
                    "category": "pipeline",
                    "severity": "ERROR",
                    "message": f"terminal: {message}",
                }
            ],
            source="flow_hook",
        )
    except Exception as exc:
        _log.warning("voicenotes.flow.terminal_emit_failed error=%r", exc)


# ---------------------------------------------------------------------------
# Per-file pipeline
# ---------------------------------------------------------------------------


def _process_one_file(drive_file_id: str) -> dict[str, Any]:
    """Run the full per-file pipeline. Returns a result dict on success.

    Raises whatever the underlying tasks raise — caller catches and
    decides whether to abort the batch or continue.
    """
    audio_bytes = download_audio(drive_file_id)
    transcription = transcribe(audio_bytes, drive_file_id=drive_file_id)
    extracted = extract(transcription.text, drive_file_id=drive_file_id)
    task_id = post_task(extracted, drive_file_id=drive_file_id)
    archive_audio(drive_file_id)
    return {
        "drive_file_id": drive_file_id,
        "todoist_task_id": task_id,
        "needs_review": extracted.needs_review,
        "transcription_cost_usd": float(
            getattr(transcription, "cost_usd_estimate", None) or 0.0
        ),
    }


def _failed_step_from_exception(exc: BaseException) -> str:
    """Walk the traceback to identify which task raised. ``unknown`` if unclear."""
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


# ---------------------------------------------------------------------------
# Main flow
# ---------------------------------------------------------------------------


@flow(
    name="voicenotes-ingest",
    on_crashed=[_emit_terminal_failure],
    on_failure=[_emit_terminal_failure],
)
def voicenotes_ingest() -> dict[str, Any]:
    """Scan ``voice-inbox/`` root and process every audio file found.

    Returns
    -------
    Summary dict::

        {
            "files_processed": int,
            "files_failed": int,
            "files_seen": int,
            "duration_sec": float,
            "total_cost_usd_estimate": float,
            "flow_run_id": str,
            "results": [<per-file success dicts>],
            "failures": [<per-file failure dicts>],
        }

    Per-file failures are isolated — a bad file does not abort the
    batch. Failed files stay in ``voice-inbox/`` for the next watcher
    cycle to retry.
    """
    # Fail fast and loud if voicenotes config is missing. Module import
    # accepts empty defaults so the parent cog can boot without
    # voicenotes secrets — see voicenotes.config docstring and ADR-004.
    require_voicenotes_settings()

    flow_logger = _flow_logger()
    started_at = datetime.now(UTC)
    flow_run_id = _current_flow_run_id()

    flow_logger.info("voicenotes.flow.start")
    _logger.info(
        "voicenotes.flow.start",
        category="pipeline",
        context={"flow_run_id": flow_run_id},
    )

    drive = get_drive_client()
    inbox_id = settings.google_drive_voice_inbox_folder_id

    # Acquire named runtime concurrency slot — see PIPE-009.
    # Deployment-level concurrency_limit=1 is insufficient on its own;
    # the runtime slot is what actually serializes scanning of the
    # shared Drive folder.
    with concurrency(_CONCURRENCY_SLOT, occupy=1):
        all_entries = drive.list_files(inbox_id)
        # Skip subfolders (processed/, rejected/, etc.).
        audio_files = [
            f
            for f in all_entries
            if getattr(f, "mime_type", None) != _DRIVE_FOLDER_MIME
        ]
        files_seen = len(audio_files)

        flow_logger.info(f"voicenotes.flow.scan files_seen={files_seen}")
        _logger.info(
            "voicenotes.flow.scan",
            category="pipeline",
            context={"files_seen": files_seen, "flow_run_id": flow_run_id},
        )

        results: list[dict[str, Any]] = []
        failures: list[dict[str, Any]] = []

        for f in audio_files:
            file_id = getattr(f, "id", None)
            if not isinstance(file_id, str):
                continue
            try:
                results.append(_process_one_file(file_id))
            except Exception as exc:
                failed_at = _failed_step_from_exception(exc)
                failures.append(
                    {
                        "drive_file_id": file_id,
                        "name": getattr(f, "name", None),
                        "failed_at_task": failed_at,
                        "error": str(exc),
                        "error_type": type(exc).__name__,
                    }
                )
                flow_logger.error(
                    f"voicenotes.flow.file_failure drive_file_id={file_id} "
                    f"failed_at={failed_at} error={exc!r}"
                )
                _logger.error(
                    "voicenotes.flow.file_failure",
                    category="pipeline",
                    context={
                        "drive_file_id": file_id,
                        "failed_at_task": failed_at,
                        "error": str(exc),
                        "error_type": type(exc).__name__,
                        "flow_run_id": flow_run_id,
                    },
                )
                # Continue — bad files stay in inbox, watcher retriggers.

        # Aggregate emit_evaluation: one record per batch, with per-file
        # failures as findings. Always emits, even on empty batches, so
        # we have a heartbeat record for every scheduled cycle.
        success = len(failures) == 0
        findings: list[dict[str, Any]] = [
            {
                "category": "pipeline",
                "severity": "ERROR",
                "message": fail["error"],
                "drive_file_id": fail["drive_file_id"],
                "failed_at_task": fail["failed_at_task"],
            }
            for fail in failures
        ]
        emit_evaluation(
            flow_run_id=flow_run_id,
            drive_file_id="batch",
            success=success,
            findings=findings,
        )

        # Opportunistic cleanup. Runs at the end of every ingest cycle
        # so retention sweeps stay in sync with actual usage and we
        # avoid paying for a daily cron tick that often has nothing
        # to do. Failures here are swallowed — cleanup is best-effort
        # housekeeping and must not fail an otherwise-successful
        # ingest run. Called via ``.fn()`` to skip the Prefect
        # subflow boilerplate; we just want the function body.
        try:
            cleanup_summary = voicenotes_cleanup.fn()
            flow_logger.info(
                "voicenotes.flow.cleanup_completed "
                f"deleted={cleanup_summary.get('deleted', 0)} "
                f"failed={cleanup_summary.get('failed', 0)}"
            )
        except Exception as cleanup_exc:
            flow_logger.warning(f"voicenotes.flow.cleanup_failed error={cleanup_exc!r}")
            _logger.warning(
                "voicenotes.flow.cleanup_failed",
                category="pipeline",
                context={
                    "flow_run_id": flow_run_id,
                    "error": str(cleanup_exc),
                    "error_type": type(cleanup_exc).__name__,
                },
            )

    duration_sec = (datetime.now(UTC) - started_at).total_seconds()
    total_cost_usd = sum(r.get("transcription_cost_usd", 0.0) for r in results)
    summary: dict[str, Any] = {
        "files_processed": len(results),
        "files_failed": len(failures),
        "files_seen": files_seen,
        "duration_sec": duration_sec,
        "total_cost_usd_estimate": total_cost_usd,
        "flow_run_id": flow_run_id,
        "results": results,
        "failures": failures,
    }

    flow_logger.info(
        f"voicenotes.flow.success files_processed={len(results)} "
        f"files_failed={len(failures)} duration_sec={duration_sec:.2f}"
    )
    _logger.info(
        "voicenotes.flow.success",
        category="pipeline",
        context={
            "files_processed": len(results),
            "files_failed": len(failures),
            "files_seen": files_seen,
            "duration_sec": duration_sec,
            "total_cost_usd_estimate": total_cost_usd,
            "flow_run_id": flow_run_id,
        },
    )
    return summary


if __name__ == "__main__":
    # Dev CLI. Defaults to scan-and-process; ``--file-id`` overrides
    # to process a single specific file (handy for reproducing a
    # specific failure without putting the file back in the inbox).
    import argparse

    parser = argparse.ArgumentParser(description="Run voicenotes-ingest locally.")
    parser.add_argument(
        "--file-id",
        default=None,
        help=(
            "Optional: process a specific Drive file ID instead of "
            "scanning the inbox. Useful for replaying a failure."
        ),
    )
    args = parser.parse_args()

    if args.file_id:
        result = _process_one_file(args.file_id)
    else:
        result = voicenotes_ingest()
    _logger.info(
        "voicenotes.cli.result",
        category="pipeline",
        context={"result": result},
    )
