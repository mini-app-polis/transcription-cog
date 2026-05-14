"""Tests for the `_enforce_due_date_keyword` helper.

End-to-end coverage of `extract()` (the Prefect task) and
`ClaudeClient.extract_task()` lives in `test_claude_client.py`.
"""

from __future__ import annotations

from datetime import date

from transcription_cog.voicenotes.clients.claude_client import _enforce_due_date_keyword
from transcription_cog.voicenotes.models.extracted_task import ExtractedTask


class TestEnforceDueDateKeyword:
    """Claude shouldn't set due_date without 'due' or 'by' in transcript."""

    def test_keeps_due_date_when_due_present(self):
        """The word 'due' in the transcript preserves Claude's due_date."""
        task = ExtractedTask(title="t", description="s", due_date=date(2026, 5, 15))
        result = _enforce_due_date_keyword(task, "send report due Friday")
        assert result.due_date is not None

    def test_keeps_due_date_when_by_present(self):
        """The word 'by' in the transcript preserves Claude's due_date."""
        task = ExtractedTask(title="t", description="s", due_date=date(2026, 5, 15))
        result = _enforce_due_date_keyword(task, "send report by Friday")
        assert result.due_date is not None

    def test_drops_due_date_when_neither_keyword(self):
        """No 'due' or 'by' keyword → Claude's due_date is dropped to None."""
        task = ExtractedTask(title="t", description="s", due_date=date(2026, 5, 15))
        result = _enforce_due_date_keyword(task, "I should finish this report soon")
        assert result.due_date is None

    def test_no_op_when_due_date_already_none(self):
        """When ExtractedTask.due_date is None, the helper is a no-op."""
        task = ExtractedTask(title="t", description="s")
        result = _enforce_due_date_keyword(task, "anything goes here")
        assert result.due_date is None

    def test_case_insensitive(self):
        """'BY FRIDAY' matches the same way 'by friday' does."""
        task = ExtractedTask(title="t", description="s", due_date=date(2026, 5, 15))
        result = _enforce_due_date_keyword(task, "BY FRIDAY please")
        assert result.due_date is not None

    def test_partial_word_does_not_match(self):
        """'duesday' shouldn't match 'due'."""
        task = ExtractedTask(title="t", description="s", due_date=date(2026, 5, 15))
        result = _enforce_due_date_keyword(task, "I think tuesday or wednesday")
        assert result.due_date is None
