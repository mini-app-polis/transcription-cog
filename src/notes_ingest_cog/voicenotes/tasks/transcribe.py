"""Prefect task: transcribe audio via Whisper."""

from __future__ import annotations

from prefect import task

from notes_ingest_cog.voicenotes._shared import get_logger
from notes_ingest_cog.voicenotes.clients.whisper_client import (
    TranscriptionResult,
    get_whisper_client,
)
from notes_ingest_cog.voicenotes.config import settings

_logger = get_logger("voicenotes-cog")


@task(
    name="transcribe",
    retries=settings.task_retries,
    retry_delay_seconds=settings.task_retry_delays_seconds,
)
def transcribe(
    audio_bytes: bytes,
    *,
    drive_file_id: str | None = None,
    filename_hint: str = "audio.m4a",
) -> TranscriptionResult:
    """Run Whisper on audio bytes; return raw transcript + metadata.

    Empty transcripts are not an error — Whisper returns `""` for
    silent audio and downstream Claude flags ``needs_review``.

    Args:
        audio_bytes: Raw audio.
        drive_file_id: Source Drive file id, threaded through for log
            correlation across the flow. Optional so unit tests can
            call without it.
        filename_hint: Filename hint for Whisper's decoder dispatch.
    """
    _logger.info(
        "voicenotes.transcribe.start",
        category="pipeline",
        context={
            "drive_file_id": drive_file_id,
            "bytes": len(audio_bytes),
            "filename_hint": filename_hint,
        },
    )
    try:
        result = get_whisper_client().transcribe(
            audio_bytes, filename_hint=filename_hint
        )
    except Exception as exc:
        _logger.error(
            "voicenotes.transcribe.failure",
            category="pipeline",
            context={"drive_file_id": drive_file_id, "error": str(exc)},
        )
        raise
    _logger.info(
        "voicenotes.transcribe.success",
        category="pipeline",
        context={
            "drive_file_id": drive_file_id,
            "transcript_length_chars": len(result.text),
            "audio_duration_sec": result.audio_duration_sec,
            "cost_usd_estimate": result.cost_usd_estimate,
        },
    )
    return result
