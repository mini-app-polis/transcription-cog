"""Prefect task: transcribe audio via Whisper."""

from __future__ import annotations

from prefect import task

from transcription_cog.voicenotes._shared import get_logger
from transcription_cog.voicenotes.clients.whisper_client import (
    TranscriptionResult,
    get_whisper_client,
)
from transcription_cog.voicenotes.config import settings

_logger = get_logger("voicenotes-cog")


@task(
    name="transcribe",
    retries=settings.task_retries,
    retry_delay_seconds=settings.task_retry_delays_seconds,
    # ``timeout_seconds`` is the outer guard against a stuck Whisper
    # call holding the worker through a Railway redeploy. The OpenAI
    # SDK also has its own per-request timeout
    # (``whisper_request_timeout_seconds``); this Prefect-level cap
    # additionally bounds the SDK's own internal retries so a flapping
    # provider can't keep one worker pinned indefinitely. See
    # voicenotes/config.py for the layering rationale.
    timeout_seconds=settings.whisper_task_timeout_seconds,
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
