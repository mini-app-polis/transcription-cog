"""Tests for post_task — Todoist payload composition and idempotency.

Covers TEST-002 (deduplication) of the ecosystem critical-path tests.
"""

from __future__ import annotations

from datetime import date
from unittest.mock import MagicMock

from transcription_cog.voicenotes.clients import todoist_client as todoist_mod
from transcription_cog.voicenotes.models.extracted_task import ExtractedTask
from transcription_cog.voicenotes.tasks import post_task as post_task_mod
from transcription_cog.voicenotes.tasks.post_task import (
    DRIVE_FILE_VIEW_URL_TEMPLATE,
    compose_description,
    compose_task_input,
    post_task,
)


def _fake_todoist(*, find_returns: str | None = None, create_returns: str = "td-new"):
    """Build a TodoistClient MagicMock with configurable find/create returns."""
    fake = MagicMock(spec=todoist_mod.TodoistClient)
    fake.find_task_by_drive_file_id.return_value = find_returns
    fake.create_task.return_value = create_returns
    return fake


class TestAudioFooterUrl:
    """Format of the Drive view URL used as both audio link and dedup marker."""

    def test_url_format_contains_drive_file_id(self):
        """Rendered URL is a Drive view link containing the drive_file_id verbatim."""
        url = DRIVE_FILE_VIEW_URL_TEMPLATE.format(drive_file_id="abc123")
        assert url == "https://drive.google.com/file/d/abc123/view"


class TestComposeDescription:
    """compose_description renders bold-header sections joined by blank lines."""

    def test_sparse_note_renders_prose_and_audio_section(self):
        """No auxiliary Ws → bare description prose + Audio section.

        The description body has no bold "Description" header — Todoist's
        own rendering of the task body makes "this is the description"
        obvious. Sparse notes skip Context/Peers/Timeline entirely.
        """
        extracted = ExtractedTask(title="t", description="My note body.")
        rendered = compose_description(extracted, drive_file_id="file-1")
        # Description prose leads, no header.
        assert rendered.startswith("My note body.")
        assert "**Description**" not in rendered
        # Audio section with Listen link.
        assert (
            "**Audio**\n[Listen](https://drive.google.com/file/d/file-1/view)"
            in rendered
        )
        # No auxiliary 5W sections.
        assert "**Context**" not in rendered
        assert "**Peers**" not in rendered
        assert "**Timeline**" not in rendered

    def test_metadata_sections_render_present_fields_only(self):
        """Only Ws with content get sections; absent ones are skipped entirely.

        Verifies the "skip if None" rule — absent Ws don't produce
        empty bold headers or blank values.
        """
        extracted = ExtractedTask(
            title="t",
            description="Clarify the marketing line items.",
            who="Sarah",
            when="Before Friday review",
        )
        rendered = compose_description(extracted, drive_file_id="f")
        assert "**Peers**\nSarah" in rendered
        assert "**Timeline**\nBefore Friday review" in rendered
        # No `where` was supplied → no Context section at all.
        assert "**Context**" not in rendered

    def test_section_order_is_fixed(self):
        """Section order is fixed regardless of ExtractedTask field order.

        Documented order: description prose → Context → Peers →
        Timeline → Audio. The description prose has no header
        (Todoist's own rendering makes the role obvious). The user
        scans bold headers by position, so the order can't drift.
        """
        extracted = ExtractedTask(
            title="t",
            description="d-prose",
            when="Tonight",  # declared last among the Ws here
            who="Mark",
            where="On the way home",
        )
        rendered = compose_description(extracted, drive_file_id="f")
        prose_idx = rendered.index("d-prose")
        ctx_idx = rendered.index("**Context**")
        peers_idx = rendered.index("**Peers**")
        timeline_idx = rendered.index("**Timeline**")
        audio_idx = rendered.index("**Audio**")
        assert prose_idx < ctx_idx < peers_idx < timeline_idx < audio_idx

    def test_blank_line_between_every_section(self):
        """Sections are separated by exactly one blank line (``\\n\\n``).

        This is the visual rhythm — header, blank line, value, blank
        line, next header — that makes the description scannable.
        """
        extracted = ExtractedTask(
            title="t",
            description="d",
            who="Sarah",
        )
        rendered = compose_description(extracted, drive_file_id="f")
        # No three-or-more consecutive newlines anywhere.
        assert "\n\n\n" not in rendered

    def test_audio_section_always_last(self):
        """The Audio section is always the last block in the rendered body.

        The Audio section's URL is the idempotency marker —
        ``find_task_by_drive_file_id`` scans descriptions for it.
        Keeping the section at the bottom is the visual convention.
        """
        extracted = ExtractedTask(
            title="t",
            description="d",
            who="Sarah",
        )
        rendered = compose_description(extracted, drive_file_id="zzz")
        assert rendered.rstrip().endswith(
            "**Audio**\n[Listen](https://drive.google.com/file/d/zzz/view)"
        )


class TestComposeTaskInput:
    """compose_task_input maps ExtractedTask + drive_file_id to TodoistTaskInput."""

    def test_review_label_when_needs_review(self):
        """``needs_review`` → only the 'review' label, no semantic labels.

        Review tasks are for human triage; we keep them clean of
        ad-hoc Claude-suggested labels so the user's filtered review
        view stays focused.
        """
        extracted = ExtractedTask(
            title="t",
            description="s",
            needs_review=True,
            labels=["follow-up"],  # would be dropped by the review path
        )
        out = compose_task_input(extracted, drive_file_id="f")
        assert out.labels == ("review",)

    def test_no_labels_when_clean_and_claude_returned_none(self):
        """Happy path with no Claude-suggested labels → empty labels tuple."""
        extracted = ExtractedTask(title="t", description="s", needs_review=False)
        out = compose_task_input(extracted, drive_file_id="f")
        assert out.labels == ()

    def test_claude_labels_passed_through_on_happy_path(self):
        """Happy path with Claude-suggested labels → those labels appear on Todoist.

        Order is preserved from the model (which preserves first
        occurrence per the validator's dedupe rule).
        """
        extracted = ExtractedTask(
            title="t",
            description="s",
            needs_review=False,
            labels=["errand", "admin"],
        )
        out = compose_task_input(extracted, drive_file_id="f")
        assert out.labels == ("errand", "admin")

    def test_due_date_always_forced_to_today(self, monkeypatch):
        """Even if Claude returned a due_date, the Todoist payload's
        due_date is forced to today (UTC).

        Voice notes captured today land in the user's Today view in
        Todoist by default; if the actual deadline is different, the
        user adjusts it manually after triage. Claude's extracted
        ``due_date`` is informational only.
        """
        fixed = date(2026, 5, 10)
        monkeypatch.setattr(post_task_mod, "_today", lambda: fixed)
        extracted = ExtractedTask(
            title="t", description="s", due_date=date(2026, 5, 15)
        )
        out = compose_task_input(extracted, drive_file_id="f")
        assert out.due_date == fixed

    def test_empty_title_falls_back_and_forces_review(self):
        """Empty title is replaced with the fallback and the review label is added.

        Todoist's ``content`` (= title) field has minlen=1; sending an
        empty string returns 400 ``error_tag=INVA`` and would loop
        retries forever. Claude can produce an empty title for a
        gibberish/all-filler transcript while correctly setting
        ``needs_review=true``; ``compose_task_input`` substitutes a
        fallback so the task lands in Todoist with the ``review``
        label for the user to triage.
        """
        extracted = ExtractedTask(title="", description="anything", needs_review=True)
        out = compose_task_input(extracted, drive_file_id="f")
        assert out.title.strip() != ""
        assert "review" in out.labels

    def test_whitespace_only_title_falls_back_and_forces_review(self):
        """A title that's only whitespace is treated as empty and triggers fallback.

        Even if the model emits ``"needs_review": false`` alongside a
        whitespace-only title, the Todoist API would still reject it,
        so we coerce ``needs_review`` on by adding the ``review``
        label and substituting the fallback title.
        """
        extracted = ExtractedTask(title="   ", description="s", needs_review=False)
        out = compose_task_input(extracted, drive_file_id="f")
        assert out.title.strip() != ""
        assert "review" in out.labels


class TestPostTaskHappyPath:
    """First-time post: no existing marker, create_task runs and returns the new id."""

    def test_creates_when_no_existing_task(self, monkeypatch):
        """No prior marker → create_task is called and the new task id is returned."""
        fake = _fake_todoist(find_returns=None, create_returns="td-101")
        monkeypatch.setattr(post_task_mod, "get_todoist_client", lambda: fake)

        extracted = ExtractedTask(title="Send report", description="Body.")
        result = post_task.fn(extracted, drive_file_id="drive-abc")

        assert result == "td-101"
        fake.find_task_by_drive_file_id.assert_called_once()
        fake.create_task.assert_called_once()
        # The payload to create_task should embed our marker.
        sent = fake.create_task.call_args.args[0]
        # The payload's description embeds the audio link footer with
        # the drive_file_id baked into the URL.
        assert "https://drive.google.com/file/d/drive-abc/view" in sent.description


class TestPostTaskDeduplication:
    """TEST-002: running with a pre-existing matching task → no duplicate."""

    def test_returns_existing_id_without_creating(self, monkeypatch):
        """Existing marker → return the existing task id, skip create_task."""
        fake = _fake_todoist(
            find_returns="td-existing", create_returns="td-should-not-happen"
        )
        monkeypatch.setattr(post_task_mod, "get_todoist_client", lambda: fake)

        extracted = ExtractedTask(title="t", description="s")
        result = post_task.fn(extracted, drive_file_id="dup-id")

        assert result == "td-existing"
        fake.find_task_by_drive_file_id.assert_called_once()
        fake.create_task.assert_not_called()
