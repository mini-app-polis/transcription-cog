"""Tests for ClaudeClient — JSON parsing, fallback, due-date guard.

Covers ecosystem-standards critical paths:
  - TEST-001 normalization: a representative transcript is normalized
    into an ExtractedTask whose fields match the prompt's expected
    output shape.
  - TEST-003 failure-path: malformed Claude output yields a fallback
    ExtractedTask with needs_review=True (no exception escapes).
  - TEST-004 output-shape: the result is an ExtractedTask instance
    (Pydantic model) with the documented field set.
"""

from __future__ import annotations

import json
from datetime import date
from types import SimpleNamespace
from unittest.mock import MagicMock

from transcription_cog.voicenotes.clients.claude_client import ClaudeClient
from transcription_cog.voicenotes.models.extracted_task import ExtractedTask


def _make_message(text: str, *, in_tokens: int = 100, out_tokens: int = 25):
    """Build a fake Anthropic ``Message`` shape for tests."""
    return SimpleNamespace(
        content=[SimpleNamespace(text=text)],
        usage=SimpleNamespace(input_tokens=in_tokens, output_tokens=out_tokens),
    )


def _client_with_responses(*texts: str) -> tuple[ClaudeClient, MagicMock]:
    """Build a ClaudeClient whose Anthropic SDK returns given texts in order."""
    messages_create = MagicMock(side_effect=[_make_message(t) for t in texts])
    fake = SimpleNamespace(messages=SimpleNamespace(create=messages_create))
    return ClaudeClient(anthropic_client=fake), messages_create  # type: ignore[arg-type]


class TestExtractTaskNormalization:
    """TEST-001: representative transcript → normalized ExtractedTask."""

    def test_normalizes_clean_transcript(self):
        """A clean transcript flows through Claude → ExtractedTask with all fields."""
        raw = json.dumps(
            {
                "title": "Send floor trials report to Mark",
                "description": "Send the floor trials report over to Mark.",
                "where": None,
                "who": "Mark",
                "when": None,
                "due_date": "2026-05-15",
                "needs_review": False,
            }
        )
        client, sdk = _client_with_responses(raw)

        result = client.extract_task(
            transcript="uh remind me to send the floor trials report to Mark by Friday",
            today=date(2026, 5, 8),
        )

        # TEST-004: output shape is the Pydantic model.
        assert isinstance(result.task, ExtractedTask)
        assert result.task.title == "Send floor trials report to Mark"
        assert "Mark" in result.task.description
        assert result.task.who == "Mark"
        assert result.task.due_date == date(2026, 5, 15)
        assert result.task.needs_review is False
        assert result.input_tokens == 100
        assert result.output_tokens == 25
        sdk.assert_called_once()

    def test_normalizes_multi_w_transcript(self):
        """A transcript with all 5 Ws populates the optional fields cleanly."""
        raw = json.dumps(
            {
                "title": "Follow up with Sarah on Q3 budget",
                "description": (
                    "Clarify the marketing line items in the Q3 budget Sarah "
                    "sent over earlier this week."
                ),
                "where": None,
                "who": "Sarah",
                "when": "Before the Friday review",
                "due_date": None,
                "needs_review": False,
            }
        )
        client, _ = _client_with_responses(raw)
        result = client.extract_task(
            transcript=(
                "I want to follow up with Sarah about the Q3 budget she sent "
                "over earlier this week, we need to clarify the marketing "
                "line items before the Friday review"
            ),
            today=date(2026, 5, 8),
        )
        assert result.task.who == "Sarah"
        assert result.task.when == "Before the Friday review"
        assert result.task.where is None
        assert result.task.due_date is None

    def test_strips_json_fences(self):
        """Triple-backtick ```json fences around the response are stripped."""
        raw = (
            "```json\n"
            + json.dumps(
                {
                    "title": "T",
                    "description": "S",
                    "where": None,
                    "who": None,
                    "when": None,
                    "due_date": None,
                    "needs_review": False,
                }
            )
            + "\n```"
        )
        client, _ = _client_with_responses(raw)
        result = client.extract_task(transcript="t", today=date(2026, 5, 8))
        assert result.task.title == "T"


class TestExtractTaskFailurePath:
    """TEST-003: malformed Claude output → fallback, no exception."""

    def test_invalid_json_retries_then_falls_back(self):
        """Two non-JSON responses → fallback ExtractedTask, no exception."""
        client, sdk = _client_with_responses(
            "this is not json",
            "still not json",
        )
        result = client.extract_task(
            transcript="something the user said", today=date(2026, 5, 8)
        )

        # TEST-004 shape assertion + TEST-003 continuation assertion.
        assert isinstance(result.task, ExtractedTask)
        assert result.task.needs_review is True
        assert "something the user said" in result.task.description
        assert sdk.call_count == 2

    def test_recovers_on_second_attempt(self):
        """Garbage on attempt 1 → valid JSON on retry → real ExtractedTask returned."""
        good = json.dumps(
            {
                "title": "After retry",
                "description": "Body.",
                "where": None,
                "who": None,
                "when": None,
                "due_date": None,
                "needs_review": False,
            }
        )
        client, sdk = _client_with_responses("garbage", good)
        result = client.extract_task(transcript="t", today=date(2026, 5, 8))
        assert result.task.title == "After retry"
        assert result.task.needs_review is False
        assert sdk.call_count == 2


class TestExtractTaskDueDateGuard:
    """The defensive due-date guard is the cog's "core transformation."""

    def test_drops_due_date_when_no_keyword_in_transcript(self):
        """Transcript lacks 'due'/'by' → defensive guard nullifies Claude's date."""
        raw = json.dumps(
            {
                "title": "Finish the deck",
                "description": "Finish the pitch deck this week.",
                "where": None,
                "who": None,
                "when": "This week",
                "due_date": "2026-05-15",
                "needs_review": False,
            }
        )
        client, _ = _client_with_responses(raw)
        result = client.extract_task(
            transcript="I really need to finish the deck this week",
            today=date(2026, 5, 8),
        )
        assert result.task.due_date is None

    def test_keeps_due_date_when_keyword_present(self):
        """Transcript has 'by Friday' → Claude's due_date is preserved."""
        raw = json.dumps(
            {
                "title": "Send report",
                "description": "Send the report by Friday.",
                "where": None,
                "who": None,
                "when": "By Friday",
                "due_date": "2026-05-15",
                "needs_review": False,
            }
        )
        client, _ = _client_with_responses(raw)
        result = client.extract_task(
            transcript="send the report by Friday",
            today=date(2026, 5, 8),
        )
        assert result.task.due_date == date(2026, 5, 15)


# Direct tests of the _enforce_due_date_keyword helper live in
# test_extract.py and are exercised end-to-end via the cases above.
