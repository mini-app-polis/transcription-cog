"""Prefect task: download audio file from Drive."""

from __future__ import annotations

from prefect import task

from notes_ingest_cog.voicenotes._shared import get_logger
from notes_ingest_cog.voicenotes.clients.drive_client import get_drive_client
from notes_ingest_cog.voicenotes.config import settings

_logger = get_logger("voicenotes-cog")


@task(
    name="download_audio",
    retries=settings.task_retries,
    retry_delay_seconds=settings.task_retry_delays_seconds,
)
def download_audio(drive_file_id: str) -> bytes:
    """Pull the audio file content from Drive.

    Returns the raw bytes; caller hands them to Whisper. Errors
    propagate to Prefect's retry handling.
    """
    _logger.info(
        "voicenotes.download.start",
        category="pipeline",
        context={"drive_file_id": drive_file_id},
    )
    try:
        data = get_drive_client().download_file(drive_file_id)
    except Exception as exc:
        _logger.error(
            "voicenotes.download.failure",
            category="pipeline",
            context={"drive_file_id": drive_file_id, "error": str(exc)},
        )
        raise
    _logger.info(
        "voicenotes.download.success",
        category="pipeline",
        context={
            "drive_file_id": drive_file_id,
            "bytes_downloaded": len(data),
        },
    )
    return data
