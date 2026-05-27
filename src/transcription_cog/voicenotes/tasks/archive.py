"""Prefect task: move processed audio file out of voice-inbox/ root.

Layout:
    voice-inbox/                  ← watcher-cog scans here
        processed/
            2026-05-27/
                <audio files processed on 2026-05-27>
            2026-05-28/
                <audio files processed on 2026-05-28>
            ...

Buckets are per processing-day (YYYY-MM-DD), not per month. Per-day
folders keep each batch visually grouped — operators can open the
folder for today and see exactly what ran today, instead of paging
through a month's accumulation. Monthly buckets used to be the layout
(``processed/YYYY-MM/``) but they collapsed every run in a month into
the same folder, which made the archive opaque for triage and
auditing.

Legacy monthly folders (``processed/2026-05/``) remain in place — the
cleanup flow walks every immediate child of ``processed/`` uniformly
regardless of naming, so retention deletion still drains old monthly
buckets without any migration.

watcher-cog ignores subdirectories, so a moved file cannot retrigger
the flow. The ``ensure_subfolder`` calls are idempotent — they reuse
existing folders when present.
"""

from __future__ import annotations

from datetime import UTC, datetime

from prefect import task

from transcription_cog.voicenotes._shared import get_logger
from transcription_cog.voicenotes.clients.drive_client import get_drive_client
from transcription_cog.voicenotes.config import settings

_logger = get_logger("voicenotes-cog")

# Name of the processed-audio root inside the voice-inbox folder.
_PROCESSED_FOLDER_NAME = "processed"


def _current_yyyy_mm_dd() -> str:
    """Compute the YYYY-MM-DD bucket the file should be archived into.

    Uses UTC so the bucket name is stable across operator timezones —
    two operators looking at the same archive folder see the same
    layout regardless of where they run. A run that straddles midnight
    UTC will write files into two adjacent date folders, which is the
    correct behavior: the bucket reflects when each file's archive
    step actually fired, not when the batch started.
    """
    return datetime.now(UTC).strftime("%Y-%m-%d")


def _resolve_dest_folder_id() -> str:
    """Return the Drive folder ID for ``processed/<YYYY-MM-DD>/``.

    Lazy: creates the ``processed/`` parent and the YYYY-MM-DD
    subfolder on first archive of a new day. Idempotent — same-day
    archives after the first reuse the existing folder via
    ``ensure_subfolder``.
    """
    drive = get_drive_client()
    processed_id = drive.ensure_subfolder(
        settings.google_drive_voice_inbox_folder_id,
        _PROCESSED_FOLDER_NAME,
    )
    bucket = _current_yyyy_mm_dd()
    return drive.ensure_subfolder(processed_id, bucket)


@task(
    name="archive_audio",
    retries=settings.task_retries,
    retry_delay_seconds=settings.task_retry_delays_seconds,
)
def archive_audio(drive_file_id: str) -> str:
    """Move the audio file to ``processed/YYYY-MM-DD/``.

    Returns the destination folder ID for logging.

    MUST run after ``post_task`` succeeds — running before would risk
    losing the audio if the Todoist post fails.
    """
    _logger.info(
        "voicenotes.archive.start",
        category="pipeline",
        context={"drive_file_id": drive_file_id},
    )
    try:
        dest_folder_id = _resolve_dest_folder_id()
        get_drive_client().move_file(drive_file_id, dest_folder_id)
    except Exception as exc:
        _logger.error(
            "voicenotes.archive.failure",
            category="pipeline",
            context={"drive_file_id": drive_file_id, "error": str(exc)},
        )
        raise

    _logger.info(
        "voicenotes.archive.success",
        category="pipeline",
        context={
            "drive_file_id": drive_file_id,
            "dest_folder_id": dest_folder_id,
            "bucket": _current_yyyy_mm_dd(),
        },
    )
    return dest_folder_id
