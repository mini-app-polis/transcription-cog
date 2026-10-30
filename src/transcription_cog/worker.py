"""The Lambda entrypoint: one queue message in, one run out.

transcription-cog runs on Lambda behind its own FIFO queue, declared in
mini-app-polis/infra: AWS polls the queue and invokes :func:`lambda_handler`, so nothing of ours stays awake between
recordings.

The API is the only producer. watcher-cog asks it to process one file, and
it enqueues one message:

    {"type": "transcription.run", "version": 1,
     "payload": {"mode": "<mode>", "drive_file_id": "<id>"}}

**One file per message, not one folder.** deejay-cog's message is a sweep;
this one cannot be. A single transcript's extraction has taken five
minutes, so a sweep of a few would outlive Lambda's 900-second ceiling.
watcher already knows which files changed and names them. The one mode
without a file is ``voicenotes-cleanup``, the retention sweep, which works
on the archive.

**Nothing is deleted until the work is done.** This code deletes nothing
at all: the event source mapping deletes every record the handler does not
name in ``batchItemFailures``. So "do not delete" means "name it", and
:func:`mini_app_polis.lambda_worker.process_records` names every record
whose run raised. Get that backwards and a failed run is silently
discarded — the property the queue exists to remove.

**An unrecognised message is a producer bug.** The queue is
transcription-cog's alone, so a type, version, mode or file this consumer
does not accept was enqueued wrongly. It is reported, exhausts its receives
and lands in the dead-letter queue where someone can see what produced it,
rather than being guessed at or dropped.

**One report per run, from the flow.** Each flow sends its own report,
however it ends — a run that raises sends it on the way out — so the
worker reports only what never reached a flow: a message it could not
read. What this module adds is the run id: the SQS message id, which is
also what the API answered watcher with, so a run can be traced from
watcher's log to the report without a join table.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from typing import Any

from googleapiclient.http import DEFAULT_HTTP_TIMEOUT_SEC
from mini_app_polis import load_secrets, timing
from mini_app_polis import logger as logger_mod
from mini_app_polis.api.contract import TranscriptionRunMessage
from mini_app_polis.lambda_worker import (
    UnprocessableMessage,
    all_failed,
    deadline,
    parse_message,
    process_records,
)
from mini_app_polis.sentry import init_sentry

from transcription_cog._pipeline_eval import post_run_finding
from transcription_cog.flow import process_note, process_transcript
from transcription_cog.voicenotes.flows.ingest import (
    voicenotes_cleanup_run,
    voicenotes_ingest,
)

log = logger_mod.get_logger()

#: How long before the function's timeout a run is stopped, so that its
#: report can still be sent and the handler can still return.
DEADLINE_MARGIN_SECONDS = 30

#: How much earlier still the shared library's Google retries stop. The
#: library checks its deadline between attempts, never during one, so a
#: request started just before it can run for the client's whole timeout.
#: That request is a Drive or Sheets call, whose timeout is the one
#: googleapiclient's ``build()`` gives the library's clients when no socket
#: default timeout is set.
REQUEST_HEADROOM_SECONDS: float = DEFAULT_HTTP_TIMEOUT_SEC

#: Modes that process one named file. A mode here is a mode the API's
#: ``TranscriptionRunRequest`` must also accept — the API rejects an
#: unknown one with a 422 before it is enqueued, and this refuses one that
#: got past it anyway. ``NOTE_MODES`` are produced by the API itself, never
#: requested through ``TranscriptionRunRequest``.
FILE_MODES: dict[str, Callable[..., Any]] = {
    "wcs-transcripts": process_transcript,
    "voicenotes": voicenotes_ingest,
}

#: Modes that name no file.
SWEEP_MODES: dict[str, Callable[..., Any]] = {
    "voicenotes-cleanup": voicenotes_cleanup_run,
}

#: Modes that carry their text: a note written on the site, sent
#: by the API's ``POST /v1/wcs/wiki/admin/notes``. No Drive file.
NOTE_MODES: dict[str, Callable[..., Any]] = {
    "wcs-note": process_note,
}

#: What a note payload must carry, each a non-empty string but ``title``.
_NOTE_FIELDS = ("id", "date", "instructor", "text")

# At import, not per invocation. A Lambda container is reused across
# invocations, so this runs once per cold start; initialising per call
# would pay the setup repeatedly and register duplicate integrations.
# An empty DSN in production logs an ERROR rather than passing silently
# (CD-038); the service tag tells this cog's events apart in the shared project.
init_sentry("transcription-cog")


@dataclass(frozen=True)
class Job:
    """What one message asks for."""

    mode: str
    drive_file_id: str | None = None
    note: dict[str, Any] | None = None


def _job_of(body: str) -> Job:
    """The job a message asks for. Raises UnprocessableMessage otherwise."""
    payload = parse_message(body, TranscriptionRunMessage).payload
    mode = payload.mode
    drive_file_id = payload.drive_file_id

    if mode in FILE_MODES:
        if not drive_file_id:
            raise UnprocessableMessage(f"mode {mode!r} needs a drive_file_id")
        return Job(mode=mode, drive_file_id=drive_file_id)

    if mode in SWEEP_MODES:
        if drive_file_id is not None:
            raise UnprocessableMessage(f"mode {mode!r} takes no drive_file_id")
        return Job(mode=mode)

    if mode in NOTE_MODES:
        if drive_file_id is not None:
            raise UnprocessableMessage(f"mode {mode!r} takes no drive_file_id")
        note = payload.note
        if note is None:
            raise UnprocessableMessage(f"mode {mode!r} needs a note object")
        missing = [k for k in _NOTE_FIELDS if not note.get(k)]
        if missing:
            raise UnprocessableMessage(f"mode {mode!r}: the note is missing {missing}")
        return Job(mode=mode, note=dict(note))

    supported = sorted([*FILE_MODES, *SWEEP_MODES, *NOTE_MODES])
    raise UnprocessableMessage(f"unknown mode {mode!r}; supported: {supported}")


def process_message(body: str, *, run_id: str) -> None:
    """Run the job one message asks for. Raises if it must be redelivered.

    A run that raises is retried whole. That is safe: a file an earlier
    attempt archived is gone from its inbox, and the next attempt finds
    nothing to do; a transcript stored but not archived is stored again by
    the API's upsert on its drive_file_id — the extraction is re-run and
    replaces the active one, nothing is duplicated; a voice note already
    handed to the task manager is asked for again under the same external
    id, which the task manager finds before creating anything.
    """
    job = _job_of(body)
    log.info(
        "worker: run %s mode=%s drive_file_id=%s", run_id, job.mode, job.drive_file_id
    )
    if job.note is not None:
        NOTE_MODES[job.mode](job.note, run_id=run_id)
    elif job.drive_file_id is not None:
        FILE_MODES[job.mode](job.drive_file_id, run_id=run_id)
    else:
        SWEEP_MODES[job.mode](run_id=run_id)


def _report_unprocessable(exc: BaseException, run_id: str) -> None:
    """Say a message could not be read, in the one place someone is watching.

    Only this case: no flow ran, so no flow reported. A run that failed
    reported itself on its way out, and a second message here would be two
    reports of one run.
    """
    try:
        post_run_finding(
            "transcription-cog",
            "ERROR",
            f"an unprocessable message failed: {type(exc).__name__}: {exc}",
            source="queue_consumer",
            run_id=run_id,
        )
    except Exception:  # noqa: BLE001 — the notification is not the job
        log.exception("worker: could not report the unprocessable message")


def _mode_label(body: str) -> str:
    """The message's mode, for the timing line. Never raises."""
    try:
        return _job_of(body).mode
    except Exception:  # noqa: BLE001 — a label must not fail the run
        return "unreadable"


def _attempt(record: dict[str, Any]) -> str:
    """Which receive of this message this is, or "?" when unknown."""
    return str((record.get("attributes") or {}).get("ApproximateReceiveCount", "?"))


def lambda_handler(event: dict[str, Any], context: Any) -> dict[str, Any]:
    """Run each record's job, and name the records that must come back.

    Never raises. An exception escaping here fails the whole batch; at
    ``batch_size = 1`` that looks identical to reporting the one record,
    right up until the batch size changes. Reporting per record is correct
    at every size, and ``ReportBatchItemFailures`` on the mapping is what
    makes this return shape mean something.
    """
    # Settings first: a warm container would otherwise run on what it read
    # at cold start, and a credential rotated in Doppler would keep failing
    # until the next one. Without its settings nothing can run, so every
    # record goes back.
    try:
        load_secrets(refresh=True)
    except Exception:  # noqa: BLE001 — nothing can run without its settings
        log.exception("worker: could not refresh settings from SSM")
        return all_failed(event)

    def handle(record: dict[str, Any]) -> None:
        message_id = str(record.get("messageId") or "")
        body = record.get("body") or ""
        attempt = _attempt(record)

        # One timing line per record: how much of the run was waiting,
        # and on what (mini_app_polis.timing).
        with timing.invocation(
            cog="transcription", mode=_mode_label(body), attempt=attempt
        ) as timed:
            try:
                with deadline(
                    context,
                    margin_seconds=DEADLINE_MARGIN_SECONDS,
                    request_headroom_seconds=REQUEST_HEADROOM_SECONDS,
                ):
                    process_message(body, run_id=message_id)
            except UnprocessableMessage:
                timed.label(outcome="unprocessable")
                raise
            except BaseException:
                timed.label(outcome="failed")
                # Already reported by the flow; see _report_unprocessable.
                log.exception("worker: run failed (attempt %s)", attempt)
                raise
            timed.label(outcome="ok")

    def on_unprocessable(exc: UnprocessableMessage, record: dict[str, Any]) -> None:
        # Once, on the first receive. Every later receive fails the same way,
        # and where it ends up — the dead-letter queue — has an alarm of its
        # own; five reports of one bad message is noise.
        if _attempt(record) in ("1", "?"):
            _report_unprocessable(exc, str(record.get("messageId") or ""))

    result = process_records(event, handle, on_unprocessable=on_unprocessable)
    failures = result["batchItemFailures"]
    if failures:
        records = event.get("Records", []) if isinstance(event, dict) else []
        log.warning(
            "worker: %d of %d record(s) returned to the queue",
            len(failures),
            len(records),
        )
    # Anything absent from this list is deleted by the mapping.
    return result
