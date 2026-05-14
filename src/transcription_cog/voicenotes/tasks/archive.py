"""Prefect task: move processed audio file out of voice-inbox/ root.

Layout:
    voice-inbox/                  ← watcher-cog scans here
        processed/
            2026-05/
                <audio files>
            2026-06/
                ...

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


def _current_yyyy_mm() -> str:
    """Compute the YYYY-MM bucket the file should be archived into.

    Uses UTC to keep behavior consistent across timezones / DST.
    """
    return datetime.now(UTC).strftime("%Y-%m")


def _resolve_dest_folder_id() -> str:
    """Return the Drive folder ID for ``processed/<YYYY-MM>/``.

    Lazy: creates the ``processed/`` parent and the YYYY-MM subfolder
    on first archive of a new month. Idempotent.
    """
    drive = get_drive_client()
    processed_id = drive.ensure_subfolder(
        settings.google_drive_voice_inbox_folder_id,
        _PROCESSED_FOLDER_NAME,
    )
    bucket = _current_yyyy_mm()
    return drive.ensure_subfolder(processed_id, bucket)


@task(
    name="archive_audio",
    retries=settings.task_retries,
    retry_delay_seconds=settings.task_retry_delays_seconds,
)
def archive_audio(drive_file_id: str) -> str:
    """Move the audio file to ``processed/YYYY-MM/``.

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
            "bucket": _current_yyyy_mm(),
        },
    )
    return dest_folder_id
