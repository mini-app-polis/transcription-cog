"""Anthropic Claude API client for the extraction step.

Calls Claude (configured model defaults to Sonnet 4.6) with the prompt
from ``src/transcription_cog/voicenotes/prompts/extract.md``, parses the JSON
response into an ``ExtractedTask``.

Behavior summary:
  - One-shot call to Claude with the rendered prompt.
  - On JSON parse failure: one retry with an explicit "return only
    valid JSON" follow-up.
  - On second parse failure: return a fallback ExtractedTask with
    ``needs_review=True`` so the pipeline never silently drops a note.
  - Defensive due-date guard (``_enforce_due_date_keyword``) drops
    Claude's due_date if the original transcript doesn't contain
    "due" or "by". Documented in docs/PROMPT.md.
"""

from __future__ import annotations

import importlib.resources
import json
import re
from dataclasses import dataclass
from datetime import date

from anthropic import Anthropic
from pydantic import ValidationError

from transcription_cog.voicenotes._shared import get_logger
from transcription_cog.voicenotes.config import settings
from transcription_cog.voicenotes.models.extracted_task import ExtractedTask

_logger = get_logger("voicenotes-cog")

# Sonnet 4.6 public pricing as of 2026 (per million tokens). Used for
# cost-estimate logging only.
_SONNET_INPUT_USD_PER_MTOK = 3.0
_SONNET_OUTPUT_USD_PER_MTOK = 15.0


@dataclass(frozen=True)
class ExtractionResult:
    """Wraps ExtractedTask with metadata useful for logging."""

    task: ExtractedTask
    input_tokens: int
    output_tokens: int
    cost_usd_estimate: float
    raw_response_preview: str
    """First ~200 chars of the raw Claude response, for debugging."""


def _load_extract_prompt_template() -> str:
    """Read the prompt template from package data."""
    return (
        importlib.resources.files("transcription_cog.voicenotes.prompts")
        .joinpath("extract.md")
        .read_text(encoding="utf-8")
    )


def _strip_json_fences(text: str) -> str:
    """Strip ```json ... ``` fences if Claude wrapped the JSON anyway."""
    text = text.strip()
    if text.startswith("```"):
        text = re.sub(r"^```(?:json)?\s*", "", text)
        text = re.sub(r"\s*```$", "", text)
    return text.strip()


class ClaudeClient:
    """Thin wrapper around Anthropic's API for the extraction call."""

    def __init__(self, *, anthropic_client: Anthropic | None = None) -> None:
        self._anthropic = anthropic_client or Anthropic(
            api_key=settings.anthropic_api_key
        )
        self._prompt_template = _load_extract_prompt_template()

    def extract_task(self, transcript: str, today: date) -> ExtractionResult:
        """Run the extraction prompt and return a parsed ExtractedTask.

        Args:
            transcript: Whisper output. May be empty.
            today: Reference date for resolving "due Friday"-style phrases.

        Returns:
            ExtractionResult with the parsed task and metadata.

        Raises:
            anthropic.APIError on transient errors (Prefect will retry).
            Does NOT raise on JSON parse failure — returns a fallback
            ``ExtractedTask`` with ``needs_review=True`` instead.
        """
        prompt = self._prompt_template.format(
            today=today.isoformat(),
            transcript=transcript,
        )

        _logger.info(
            "claude.start",
            category="api",
            context={
                "model": settings.claude_model,
                "transcript_length_chars": len(transcript),
            },
        )

        first = self._anthropic.messages.create(
            model=settings.claude_model,
            max_tokens=512,
            messages=[{"role": "user", "content": prompt}],
        )
        raw_text = self._collect_text(first)
        usage_in = getattr(first.usage, "input_tokens", 0) if first.usage else 0
        usage_out = getattr(first.usage, "output_tokens", 0) if first.usage else 0

        parsed = self._try_parse(raw_text)

        if parsed is None:
            _logger.warning(
                "claude.parse_retry",
                category="data",
                context={"raw_preview": raw_text[:200]},
            )
            second = self._anthropic.messages.create(
                model=settings.claude_model,
                max_tokens=512,
                messages=[
                    {"role": "user", "content": prompt},
                    {"role": "assistant", "content": raw_text},
                    {
                        "role": "user",
                        "content": (
                            "That was not valid JSON. Return ONLY the JSON "
                            "object that matches the schema described in "
                            "the original instructions. No preamble, no "
                            "markdown fences."
                        ),
                    },
                ],
            )
            raw_text = self._collect_text(second)
            usage_in += getattr(second.usage, "input_tokens", 0) if second.usage else 0
            usage_out += (
                getattr(second.usage, "output_tokens", 0) if second.usage else 0
            )
            parsed = self._try_parse(raw_text)

        if parsed is None:
            _logger.error(
                "claude.parse_failure",
                category="data",
                context={"raw_preview": raw_text[:200]},
            )
            parsed = _fallback_task(transcript)

        # Defensive: drop due_date if the transcript didn't actually say
        # "due" or "by". Claude is occasionally optimistic about implicit
        # deadlines.
        parsed = _enforce_due_date_keyword(parsed, transcript)

        cost_usd = self._estimate_cost(usage_in, usage_out)

        _logger.info(
            "claude.success",
            category="api",
            context={
                "input_tokens": usage_in,
                "output_tokens": usage_out,
                "cost_usd_estimate": cost_usd,
                "needs_review": parsed.needs_review,
            },
        )

        return ExtractionResult(
            task=parsed,
            input_tokens=usage_in,
            output_tokens=usage_out,
            cost_usd_estimate=cost_usd,
            raw_response_preview=raw_text[:200],
        )

    # ------------------------------------------------------------------
    # Internals
    # ------------------------------------------------------------------

    @staticmethod
    def _collect_text(message: object) -> str:
        """Concatenate the text blocks of an Anthropic message."""
        content = getattr(message, "content", None) or []
        parts: list[str] = []
        for block in content:
            text = getattr(block, "text", None)
            if isinstance(text, str):
                parts.append(text)
        return "".join(parts)

    @staticmethod
    def _try_parse(raw_text: str) -> ExtractedTask | None:
        try:
            obj = json.loads(_strip_json_fences(raw_text))
        except json.JSONDecodeError:
            return None
        try:
            return ExtractedTask.model_validate(obj)
        except ValidationError:
            return None

    @staticmethod
    def _estimate_cost(input_tokens: int, output_tokens: int) -> float:
        return (
            input_tokens / 1_000_000 * _SONNET_INPUT_USD_PER_MTOK
            + output_tokens / 1_000_000 * _SONNET_OUTPUT_USD_PER_MTOK
        )


def _fallback_task(transcript: str) -> ExtractedTask:
    """Construct a graceful-degrade ExtractedTask when Claude fails twice.

    The lost-note guarantee is the system's North Star — emitting *some*
    task with ``needs_review=True`` is strictly better than dropping
    the note. The user can listen to the audio and decide.

    The fallback embeds a transcript snippet (truncated to 200 chars)
    in the description so the user has something to work from when
    triaging the ``review``-labeled task. The 5W fields are left null
    — there's no reliable way to extract them when the model itself
    failed.
    """
    snippet = transcript.strip()[:200] or "Could not extract task content."
    return ExtractedTask(
        title="Voice note",
        description=snippet,
        due_date=None,
        needs_review=True,
    )


def _enforce_due_date_keyword(task: ExtractedTask, transcript: str) -> ExtractedTask:
    """Drop due_date if transcript doesn't contain 'due' or 'by'.

    Defensive against Claude hallucinating dates from implicit
    deadlines. See docs/PROMPT.md "Tuning notes" for the rationale.
    """
    if task.due_date is None:
        return task
    lower = transcript.lower()
    if " due " in f" {lower} " or " by " in f" {lower} ":
        return task
    return task.model_copy(update={"due_date": None})


# ---------------------------------------------------------------------------
# Module-level singleton
# ---------------------------------------------------------------------------

_client: ClaudeClient | None = None


def get_claude_client() -> ClaudeClient:
    """Return the lazily-instantiated module-level ClaudeClient singleton."""
    global _client
    if _client is None:
        _client = ClaudeClient()
    return _client


def reset_claude_client() -> None:
    """Force a new client on next call. Use from tests or key rotations."""
    global _client
    _client = None
