"""The Lambda entrypoint: one queue message in, one run out.

transcription-cog used to register a Prefect router deployment and poll
Prefect Cloud for runs from a resident Railway container. It now runs on
Lambda behind its own queue, ``transcription-jobs``: AWS polls the queue
and invokes :func:`lambda_handler`, so nothing of ours stays awake between
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
name in ``batchItemFailures``. So "do not delete" means "name it", and every
failure below appends to that list. Get that backwards and a failed run is
silently discarded — the property the queue exists to remove.

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

import json
import os
from collections.abc import Callable
from dataclasses import dataclass
from typing import Any

import sentry_sdk
from mini_app_polis import logger as logger_mod
from mini_app_polis.environment import current_environment

from transcription_cog._deadline import deadline
from transcription_cog._pipeline_eval import post_run_finding
from transcription_cog.flow import process_transcript
from transcription_cog.voicenotes.flows.ingest import (
    voicenotes_cleanup_run,
    voicenotes_ingest,
)

log = logger_mod.get_logger()

#: Must match api-kaianolevine-com's transcription_dispatch. A mismatch is
#: a message this consumer refuses rather than misreads.
MESSAGE_VERSION = 1
TYPE_RUN = "transcription.run"

#: Modes that process one named file. A mode here is a mode the API's
#: ``TranscriptionRunRequest`` must also accept — the API rejects an
#: unknown one with a 422 before it is enqueued, and this refuses one that
#: got past it anyway.
FILE_MODES: dict[str, Callable[..., Any]] = {
    "wcs-transcripts": process_transcript,
    "voicenotes": voicenotes_ingest,
}

#: Modes that name no file.
SWEEP_MODES: dict[str, Callable[..., Any]] = {
    "voicenotes-cleanup": voicenotes_cleanup_run,
}

# At import, not per invocation. A Lambda container is reused across
# invocations, so this runs once per cold start; initialising per call
# would pay the setup repeatedly and register duplicate integrations.
sentry_sdk.init(
    dsn=os.getenv("SENTRY_DSN"),
    environment=current_environment().value,
)


class UnprocessableMessage(RuntimeError):
    """The message cannot be handled by this consumer, ever.

    Distinct from a run that failed: retrying will not help. It is still
    returned to the queue rather than dropped, so it reaches the dead-letter
    queue and someone sees what produced it.
    """


@dataclass(frozen=True)
class Job:
    """What one message asks for."""

    mode: str
    drive_file_id: str | None = None


def _job_of(body: str) -> Job:
    """The job a message asks for. Raises UnprocessableMessage otherwise."""
    try:
        message = json.loads(body)
    except ValueError as exc:
        raise UnprocessableMessage(f"body is not JSON: {exc}") from exc
    if not isinstance(message, dict):
        raise UnprocessableMessage("body is not an object")

    version = message.get("version")
    if version != MESSAGE_VERSION:
        raise UnprocessableMessage(
            f"message version {version!r}, this consumer speaks {MESSAGE_VERSION}"
        )

    kind = message.get("type")
    if kind != TYPE_RUN:
        raise UnprocessableMessage(f"unknown message type {kind!r}")

    payload = message.get("payload")
    if not isinstance(payload, dict):
        raise UnprocessableMessage("message carries no payload object")

    mode = payload.get("mode")
    drive_file_id = payload.get("drive_file_id")

    if mode in FILE_MODES:
        if not isinstance(drive_file_id, str) or not drive_file_id:
            raise UnprocessableMessage(f"mode {mode!r} needs a drive_file_id")
        return Job(mode=mode, drive_file_id=drive_file_id)

    if mode in SWEEP_MODES:
        if drive_file_id is not None:
            raise UnprocessableMessage(f"mode {mode!r} takes no drive_file_id")
        return Job(mode=mode)

    supported = sorted([*FILE_MODES, *SWEEP_MODES])
    raise UnprocessableMessage(f"unknown mode {mode!r}; supported: {supported}")


def process_message(body: str, *, run_id: str) -> None:
    """Run the job one message asks for. Raises if it must be redelivered.

    A run that raises is retried whole. That is safe: a file an earlier
    attempt archived is no longer in its inbox, and the next attempt finds
    nothing to do; a transcript stored but not archived is stored again by
    the API's upsert on its drive_file_id — the extraction is re-run and
    replaces the active one, nothing is duplicated; a voice note already
    posted is found by its Asana external id.
    """
    job = _job_of(body)
    log.info(
        "worker: run %s mode=%s drive_file_id=%s", run_id, job.mode, job.drive_file_id
    )
    if job.drive_file_id is not None:
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


def lambda_handler(event: dict[str, Any], context: Any) -> dict[str, Any]:
    """Run each record's job, and name the records that must come back.

    Never raises. An exception escaping here fails the whole batch; at
    ``batch_size = 1`` that looks identical to reporting the one record,
    right up until the batch size changes. Reporting per record is correct
    at every size, and ``ReportBatchItemFailures`` on the mapping is what
    makes this return shape mean something.
    """
    records = event.get("Records", []) if isinstance(event, dict) else []
    failures: list[dict[str, str]] = []

    for record in records:
        message_id = str(record.get("messageId") or "")
        body = record.get("body") or ""
        attempt = (record.get("attributes") or {}).get("ApproximateReceiveCount", "?")

        try:
            with deadline(context):
                process_message(body, run_id=message_id)
        except UnprocessableMessage as exc:
            log.error("worker: unprocessable message (attempt %s): %s", attempt, exc)
            _report_unprocessable(exc, message_id)
            failures.append({"itemIdentifier": message_id})
        except Exception:  # noqa: BLE001 — every failure is a retry
            # Already reported by the flow; see _report_unprocessable.
            log.exception("worker: run failed (attempt %s)", attempt)
            failures.append({"itemIdentifier": message_id})

    if failures:
        log.warning(
            "worker: %d of %d record(s) returned to the queue",
            len(failures),
            len(records),
        )

    # Anything absent from this list is deleted by the mapping. An empty
    # list means "all of it is done" — true only because every failure
    # above appended to it.
    return {"batchItemFailures": failures}
