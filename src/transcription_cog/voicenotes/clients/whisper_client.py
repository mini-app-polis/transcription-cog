"""OpenAI Whisper API client.

Single responsibility: turn audio bytes into a transcript string. No
post-processing; raw output flows downstream to the Claude extraction
step.

Uses the openai>=1.50 SDK. The audio is passed as a tuple of
(filename, bytes, mime) so the SDK can dispatch to the right decoder
based on extension.
"""

from __future__ import annotations

import io
from dataclasses import dataclass

from openai import OpenAI

from transcription_cog.voicenotes._shared import get_logger
from transcription_cog.voicenotes.config import settings

_logger = get_logger("voicenotes-cog")

# Whisper public pricing as of 2026 — $0.006/min. Update if pricing
# shifts.
_WHISPER_USD_PER_MINUTE = 0.006


@dataclass(frozen=True)
class TranscriptionResult:
    """What the Whisper client returns to its caller."""

    text: str
    """Raw transcript. May be empty string if Whisper detected no speech."""

    audio_duration_sec: float | None = None
    """Audio duration as reported by Whisper (or None if not available)."""

    cost_usd_estimate: float | None = None
    """Best-effort cost estimate for logging. Whisper is $0.006/min."""


class WhisperClient:
    """Thin wrapper around OpenAI's Whisper API."""

    def __init__(self, *, openai_client: OpenAI | None = None) -> None:
        # Lazy: tests can inject a fake client; production builds a real
        # one using the configured API key.
        self._openai = openai_client or OpenAI(api_key=settings.openai_api_key)

    def transcribe(
        self,
        audio_bytes: bytes,
        filename_hint: str = "audio.m4a",
    ) -> TranscriptionResult:
        """Transcribe audio.

        Args:
            audio_bytes: Raw audio file content.
            filename_hint: Filename passed in the multipart upload.
                Whisper uses the extension to dispatch to the right
                decoder; .m4a, .mp3, .wav all work.

        Returns:
            TranscriptionResult.

        Raises:
            openai.APIError on API errors (Prefect will retry).
        """
        _logger.info(
            "whisper.start",
            category="api",
            context={
                "bytes": len(audio_bytes),
                "filename_hint": filename_hint,
                "model": settings.whisper_model,
            },
        )

        file_tuple = (filename_hint, io.BytesIO(audio_bytes), "audio/mpeg")

        # `verbose_json` gives us audio_duration so we can compute cost.
        response = self._openai.audio.transcriptions.create(
            model=settings.whisper_model,
            file=file_tuple,  # type: ignore[arg-type]
            response_format="verbose_json",
        )

        text = getattr(response, "text", "") or ""
        duration_sec = getattr(response, "duration", None)
        cost_usd = (
            (duration_sec / 60.0) * _WHISPER_USD_PER_MINUTE
            if duration_sec is not None
            else None
        )

        _logger.info(
            "whisper.success",
            category="api",
            context={
                "transcript_length_chars": len(text),
                "audio_duration_sec": duration_sec,
                "cost_usd_estimate": cost_usd,
            },
        )

        return TranscriptionResult(
            text=text,
            audio_duration_sec=duration_sec,
            cost_usd_estimate=cost_usd,
        )


# Singleton accessor for symmetry with other clients.
_client: WhisperClient | None = None


def get_whisper_client() -> WhisperClient:
    """Return the lazily-instantiated module-level WhisperClient singleton."""
    global _client
    if _client is None:
        _client = WhisperClient()
    return _client


def reset_whisper_client() -> None:
    """Force a new client on next call.

    Use from tests or after explicit auth/key rotation.
    """
    global _client
    _client = None
