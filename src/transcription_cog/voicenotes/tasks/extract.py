"""Prefect task: extract structured task fields from raw transcript via Claude."""

from __future__ import annotations

from datetime import UTC, date, datetime

from prefect import task

from transcription_cog.voicenotes._shared import get_logger
from transcription_cog.voicenotes.clients.claude_client import get_claude_client
from transcription_cog.voicenotes.config import settings
from transcription_cog.voicenotes.models.extracted_task import ExtractedTask

_logger = get_logger("voicenotes-cog")


def _empty_transcript_fallback(transcript: str) -> ExtractedTask:
    """Build a `needs_review=True` task locally without calling Claude.

    Whisper returns `""` for silent audio and sometimes whitespace-only
    output for ambient noise. There's no useful task to extract — so
    short-circuit, save the API call, and let post_task tag it 'review'
    so the user can listen if curious. Lost-note guarantee: still
    creates a Todoist task.
    """
    snippet = transcript.strip()[:200] or "Recording contained no audible speech."
    return ExtractedTask(
        title="Empty recording",
        description=snippet,
        due_date=None,
        needs_review=True,
    )


@task(
    name="extract",
    retries=settings.extract_task_retries,
    retry_delay_seconds=settings.extract_task_retry_delays_seconds,
)
def extract(
    transcript: str,
    today: date | None = None,
    *,
    drive_file_id: str | None = None,
) -> ExtractedTask:
    """Run extraction prompt; return structured ExtractedTask.

    The Claude client itself handles JSON-parse retries and falls back
    to ``needs_review=True`` on total parse failure. This task lets
    transport-level errors propagate so Prefect's retry policy can
    take over.

    Empty / whitespace-only transcripts are short-circuited locally
    without calling Claude.

    Args:
        transcript: Whisper output.
        today: Reference date for due-date resolution. Defaults to
            UTC today.
        drive_file_id: Source Drive file id, threaded through for log
            correlation. Optional so unit tests can call without it.
    """
    today = today or datetime.now(UTC).date()
    transcript_len = len(transcript)
    is_empty = not transcript.strip()

    _logger.info(
        "voicenotes.extract.start",
        category="pipeline",
        context={
            "drive_file_id": drive_file_id,
            "transcript_length_chars": transcript_len,
            "empty_transcript": is_empty,
        },
    )

    if is_empty:
        task_obj = _empty_transcript_fallback(transcript)
        _logger.info(
            "voicenotes.extract.success",
            category="pipeline",
            context={
                "drive_file_id": drive_file_id,
                "input_tokens": 0,
                "output_tokens": 0,
                "cost_usd_estimate": 0.0,
                "needs_review": True,
                "skipped_claude": True,
            },
        )
        return task_obj

    try:
        result = get_claude_client().extract_task(transcript, today)
    except Exception as exc:
        _logger.error(
            "voicenotes.extract.failure",
            category="pipeline",
            context={"drive_file_id": drive_file_id, "error": str(exc)},
        )
        raise

    _logger.info(
        "voicenotes.extract.success",
        category="pipeline",
        context={
            "drive_file_id": drive_file_id,
            "input_tokens": result.input_tokens,
            "output_tokens": result.output_tokens,
            "cost_usd_estimate": result.cost_usd_estimate,
            "needs_review": result.task.needs_review,
            "skipped_claude": False,
        },
    )
    return result.task
