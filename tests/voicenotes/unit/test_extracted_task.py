"""Tests for ExtractedTask Pydantic model."""

from __future__ import annotations

from datetime import date

from transcription_cog.voicenotes.models.extracted_task import ExtractedTask


class TestExtractedTask:
    """Construction, validation, and field defaults for ExtractedTask."""

    def test_minimal_valid(self):
        """Title + description only: optional Ws and due_date all default None."""
        task = ExtractedTask(title="Do thing", description="Do the thing.")
        assert task.title == "Do thing"
        assert task.description == "Do the thing."
        assert task.where is None
        assert task.who is None
        assert task.when is None
        assert task.due_date is None
        assert task.needs_review is False

    def test_strips_trailing_punctuation_from_title(self):
        """Trailing period in the title is stripped by the field validator."""
        task = ExtractedTask(title="Do the thing.", description="...")
        assert task.title == "Do the thing"

    def test_long_title_passes_through_unchanged(self):
        """No length cap — long titles are preserved verbatim.

        Earlier the model enforced ``max_length=60`` and Claude
        occasionally returned titles a few chars over, which caused
        parse failures and forced the cog into a bland review
        fallback. Todoist itself accepts long titles, so the cap was
        more harm than help. The prompt still nudges Claude toward
        concise titles, but no hard limit is enforced.
        """
        long_title = "Grab badge, drop off laptop with IT, and meet Sarah for coffee"
        task = ExtractedTask(title=long_title, description="...")
        assert task.title == long_title

    def test_description_stripped(self):
        """Leading/trailing whitespace is stripped from the description."""
        task = ExtractedTask(title="t", description="  hello  ")
        assert task.description == "hello"

    def test_with_due_date(self):
        """An explicit due_date is preserved on the model."""
        task = ExtractedTask(
            title="t",
            description="s",
            due_date=date(2026, 5, 15),
        )
        assert task.due_date == date(2026, 5, 15)

    def test_needs_review_default_false(self):
        """needs_review defaults to False when not specified."""
        task = ExtractedTask(title="t", description="s")
        assert task.needs_review is False

    def test_optional_w_fields_accept_strings(self):
        """The auxiliary 5W fields accept and preserve string values."""
        task = ExtractedTask(
            title="t",
            description="d",
            where="Project README",
            who="Sarah",
            when="Before Friday",
        )
        assert task.where == "Project README"
        assert task.who == "Sarah"
        assert task.when == "Before Friday"

    def test_empty_string_w_fields_coerced_to_none(self):
        """Empty strings on optional Ws are normalized to None.

        Claude sometimes returns "" instead of null for absent Ws;
        the validator coerces so the formatter's "skip if None" rule
        kicks in uniformly.
        """
        task = ExtractedTask(
            title="t",
            description="d",
            where="",
            who="   ",
            when=None,
        )
        assert task.where is None
        assert task.who is None
        assert task.when is None


class TestExtractedTaskLabels:
    """Validator behavior for the open-vocabulary `labels` field."""

    def test_labels_default_empty(self):
        """Omitting labels yields an empty list (not None)."""
        task = ExtractedTask(title="t", description="d")
        assert task.labels == []

    def test_labels_lowercased_and_kebab_cased(self):
        """Mixed-case multi-word strings are normalized to lower-kebab."""
        task = ExtractedTask(
            title="t",
            description="d",
            labels=["Follow Up", "ERRAND"],
        )
        assert task.labels == ["follow-up", "errand"]

    def test_labels_deduped_preserving_order(self):
        """Duplicates are removed; first occurrence wins."""
        task = ExtractedTask(
            title="t",
            description="d",
            labels=["code", "Code", "code"],
        )
        assert task.labels == ["code"]

    def test_labels_capped_at_three(self):
        """No more than 3 labels survive — guards against label sprawl."""
        task = ExtractedTask(
            title="t",
            description="d",
            labels=["a", "b", "c", "d", "e"],
        )
        assert task.labels == ["a", "b", "c"]

    def test_labels_drops_empty_and_whitespace(self):
        """Empty and whitespace-only entries are dropped before deduping."""
        task = ExtractedTask(
            title="t",
            description="d",
            labels=["", "  ", "errand"],
        )
        assert task.labels == ["errand"]

    def test_labels_none_becomes_empty(self):
        """``None`` (or anything not a list) coerces to ``[]``.

        Claude can occasionally return ``null`` for the ``labels``
        field; we accept it gracefully rather than failing parse.
        """
        task = ExtractedTask(title="t", description="d", labels=None)
        assert task.labels == []
