"""Tests for post_task — Asana payload composition and idempotency.

Covers TEST-002 (deduplication) of the ecosystem critical-path tests.
"""

from __future__ import annotations

from datetime import date
from unittest.mock import MagicMock

from mini_app_polis.asana import AsanaClient

from transcription_cog.voicenotes.models.extracted_task import ExtractedTask
from transcription_cog.voicenotes.tasks import post_task as post_task_mod
from transcription_cog.voicenotes.tasks.post_task import (
    DRIVE_FILE_VIEW_URL_TEMPLATE,
    compose_html_notes,
    compose_task_input,
    external_id_for,
    post_task,
    resolve_tag_gids,
    tag_names_for,
)


def _fake_asana(*, find_returns: str | None = None, create_returns: str = "as-new"):
    """Build an AsanaClient MagicMock with configurable find/create returns."""
    fake = MagicMock(spec=AsanaClient)
    fake.find_task_by_external_id.return_value = find_returns
    fake.create_task.return_value = create_returns
    fake.find_or_create_tag.side_effect = lambda name: f"tag-{name}"
    return fake


class TestAudioLinkUrl:
    """Format of the Drive view URL rendered in the Audio section."""

    def test_url_format_contains_drive_file_id(self):
        """Rendered URL is a Drive view link containing the drive_file_id verbatim."""
        url = DRIVE_FILE_VIEW_URL_TEMPLATE.format(drive_file_id="abc123")
        assert url == "https://drive.google.com/file/d/abc123/view"


class TestExternalId:
    """The idempotency key stored on the task's ``external`` field."""

    def test_external_id_is_namespaced_by_source(self):
        """Other sources will post to the same board; ids must not collide."""
        assert external_id_for("abc123") == "voicenote.abc123"


class TestComposeHtmlNotes:
    """compose_html_notes renders bold-header sections joined by blank lines."""

    def test_body_is_wrapped_in_a_single_body_element(self):
        """Asana rejects rich text that is not wrapped in ``<body>``."""
        extracted = ExtractedTask(title="t", description="My note body.")
        rendered = compose_html_notes(extracted, drive_file_id="f")
        assert rendered.startswith("<body>")
        assert rendered.endswith("</body>")
        assert rendered.count("<body>") == 1

    def test_sparse_note_renders_prose_and_audio_section(self):
        """No auxiliary Ws → bare description prose + Audio section.

        The description body has no bold "Description" header — Asana's
        own rendering of the task body makes "this is the description"
        obvious. Sparse notes skip Context/Peers/Timeline entirely.
        """
        extracted = ExtractedTask(title="t", description="My note body.")
        rendered = compose_html_notes(extracted, drive_file_id="file-1")
        assert rendered.startswith("<body>My note body.")
        assert "<strong>Description</strong>" not in rendered
        assert (
            "<strong>Audio</strong>\n"
            '<a href="https://drive.google.com/file/d/file-1/view">Listen</a>'
        ) in rendered
        assert "<strong>Context</strong>" not in rendered
        assert "<strong>Peers</strong>" not in rendered
        assert "<strong>Timeline</strong>" not in rendered

    def test_metadata_sections_render_present_fields_only(self):
        """Only Ws with content get sections; absent ones are skipped entirely."""
        extracted = ExtractedTask(
            title="t",
            description="Clarify the marketing line items.",
            who="Sarah",
            when="Before Friday review",
        )
        rendered = compose_html_notes(extracted, drive_file_id="f")
        assert "<strong>Peers</strong>\nSarah" in rendered
        assert "<strong>Timeline</strong>\nBefore Friday review" in rendered
        assert "<strong>Context</strong>" not in rendered

    def test_section_order_is_fixed(self):
        """Documented order: prose → Context → Peers → Timeline → Audio.

        The operator scans bold headers by position, so the order can't
        drift with ExtractedTask field order.
        """
        extracted = ExtractedTask(
            title="t",
            description="d-prose",
            when="Tonight",  # declared last among the Ws here
            who="Mark",
            where="On the way home",
        )
        rendered = compose_html_notes(extracted, drive_file_id="f")
        assert (
            rendered.index("d-prose")
            < rendered.index("<strong>Context</strong>")
            < rendered.index("<strong>Peers</strong>")
            < rendered.index("<strong>Timeline</strong>")
            < rendered.index("<strong>Audio</strong>")
        )

    def test_blank_line_between_every_section(self):
        """Sections are separated by exactly one blank line (``\\n\\n``)."""
        extracted = ExtractedTask(title="t", description="d", who="Sarah")
        rendered = compose_html_notes(extracted, drive_file_id="f")
        assert "\n\n\n" not in rendered

    def test_audio_section_always_last(self):
        """The Audio section is always the last block in the rendered body."""
        extracted = ExtractedTask(title="t", description="d", who="Sarah")
        rendered = compose_html_notes(extracted, drive_file_id="zzz")
        assert rendered.endswith(
            "<strong>Audio</strong>\n"
            '<a href="https://drive.google.com/file/d/zzz/view">Listen</a></body>'
        )

    def test_model_output_is_escaped(self):
        """A transcript containing markup must not produce a 400 from Asana.

        This is the one behavioural difference from the Todoist body,
        which was markdown and forgave stray angle brackets.
        """
        extracted = ExtractedTask(
            title="t",
            description="check value < threshold & retry",
            where="<repo>",
        )
        rendered = compose_html_notes(extracted, drive_file_id="f")
        assert "check value &lt; threshold &amp; retry" in rendered
        assert "<strong>Context</strong>\n&lt;repo&gt;" in rendered


class TestTagNames:
    """tag_names_for decides which tags apply to a note."""

    def test_review_tag_when_needs_review(self):
        """``needs_review`` → only the 'review' tag, no semantic tags.

        Review tasks are for human triage; keeping them clean of
        ad-hoc Claude-suggested tags keeps the filtered review view
        focused — and on Asana every distinct name is a real workspace
        object, so restraint matters more than it did on Todoist.
        """
        extracted = ExtractedTask(
            title="t", description="s", needs_review=True, labels=["follow-up"]
        )
        assert tag_names_for(extracted) == ("review",)

    def test_no_tags_when_clean_and_claude_returned_none(self):
        extracted = ExtractedTask(title="t", description="s", needs_review=False)
        assert tag_names_for(extracted) == ()

    def test_claude_labels_passed_through_on_happy_path(self):
        """Order is preserved from the model's dedupe-by-first-occurrence rule."""
        extracted = ExtractedTask(
            title="t", description="s", needs_review=False, labels=["errand", "admin"]
        )
        assert tag_names_for(extracted) == ("errand", "admin")

    def test_empty_title_forces_the_review_tag(self):
        """An empty title means the fallback fired, which is a triage case."""
        extracted = ExtractedTask(title="   ", description="s", needs_review=False)
        assert tag_names_for(extracted) == ("review",)


class TestResolveTagGids:
    """Tag resolution is best-effort — a tag must never strand a note."""

    def test_resolves_each_name_to_a_gid(self):
        fake = _fake_asana()
        assert resolve_tag_gids(fake, ("errand", "admin")) == (
            "tag-errand",
            "tag-admin",
        )

    def test_a_failing_tag_is_skipped_rather_than_raised(self):
        """The task body is already correct; failing over decoration would
        strand the note and its audio across every retry."""
        fake = _fake_asana()
        fake.find_or_create_tag.side_effect = [RuntimeError("boom"), "tag-admin"]
        assert resolve_tag_gids(fake, ("errand", "admin")) == ("tag-admin",)


class TestComposeTaskInput:
    """compose_task_input maps ExtractedTask + drive_file_id to AsanaTaskInput."""

    def test_targets_the_configured_project_and_section(self):
        extracted = ExtractedTask(title="t", description="s")
        out = compose_task_input(extracted, drive_file_id="f")
        assert out.project_gid == "test-project-gid"
        assert out.section_gid == "test-section-gid"

    def test_task_is_assigned_to_the_token_holder(self):
        """Voice notes are the operator's own, so they belong in My Tasks."""
        extracted = ExtractedTask(title="t", description="s")
        assert compose_task_input(extracted, drive_file_id="f").assignee == "me"

    def test_external_id_is_set_for_idempotency(self):
        extracted = ExtractedTask(title="t", description="s")
        out = compose_task_input(extracted, drive_file_id="drive-abc")
        assert out.external_id == "voicenote.drive-abc"

    def test_tag_gids_are_passed_through(self):
        extracted = ExtractedTask(title="t", description="s")
        out = compose_task_input(extracted, drive_file_id="f", tag_gids=("tag-1",))
        assert out.tag_gids == ("tag-1",)

    def test_due_date_always_forced_to_today(self, monkeypatch):
        """Even if Claude returned a due_date, the payload's due_on is
        forced to today (UTC).

        Voice notes captured today land in My Tasks for today; if the
        actual deadline differs, the operator adjusts it after triage.
        Claude's extracted ``due_date`` is informational only.
        """
        fixed = date(2026, 5, 10)
        monkeypatch.setattr(post_task_mod, "_today", lambda: fixed)
        extracted = ExtractedTask(
            title="t", description="s", due_date=date(2026, 5, 15)
        )
        assert compose_task_input(extracted, drive_file_id="f").due_on == fixed

    def test_empty_title_falls_back(self):
        """Asana rejects an empty ``name``.

        Claude can produce an empty title for a gibberish/all-filler
        transcript while correctly setting ``needs_review=true``.
        Substituting a fallback lets the task land instead of looping
        retries forever and stranding the audio in ``voice-inbox/``.
        """
        extracted = ExtractedTask(title="", description="anything", needs_review=True)
        out = compose_task_input(extracted, drive_file_id="f")
        assert out.name.strip() != ""

    def test_whitespace_only_title_falls_back(self):
        """A whitespace-only title is treated as empty.

        The fallback wording carries the review signal in the title
        itself, which matters because tag resolution is best-effort.
        """
        extracted = ExtractedTask(title="   ", description="s", needs_review=False)
        out = compose_task_input(extracted, drive_file_id="f")
        assert out.name.strip() != ""
        assert "review" in out.name.lower()


class TestPostTaskHappyPath:
    """First-time post: no existing task, create_task runs and returns the new id."""

    def test_creates_when_no_existing_task(self, monkeypatch):
        """No prior task → create_task is called and the new gid is returned."""
        fake = _fake_asana(find_returns=None, create_returns="as-101")
        monkeypatch.setattr(post_task_mod, "get_asana_client", lambda: fake)

        extracted = ExtractedTask(title="Send report", description="Body.")
        result = post_task.fn(extracted, drive_file_id="drive-abc")

        assert result == "as-101"
        fake.find_task_by_external_id.assert_called_once_with("voicenote.drive-abc")
        fake.create_task.assert_called_once()
        sent = fake.create_task.call_args.args[0]
        assert sent.external_id == "voicenote.drive-abc"
        assert "https://drive.google.com/file/d/drive-abc/view" in sent.html_notes


class TestPostTaskDeduplication:
    """TEST-002: running with a pre-existing matching task → no duplicate."""

    def test_returns_existing_id_without_creating(self, monkeypatch):
        """Existing external id → return that gid, skip create_task."""
        fake = _fake_asana(
            find_returns="as-existing", create_returns="as-should-not-happen"
        )
        monkeypatch.setattr(post_task_mod, "get_asana_client", lambda: fake)

        extracted = ExtractedTask(title="t", description="s")
        result = post_task.fn(extracted, drive_file_id="dup-id")

        assert result == "as-existing"
        fake.find_task_by_external_id.assert_called_once_with("voicenote.dup-id")
        fake.create_task.assert_not_called()

    def test_no_tags_are_resolved_on_the_duplicate_path(self, monkeypatch):
        """A duplicate should cost one request, not a tag round-trip too."""
        fake = _fake_asana(find_returns="as-existing")
        monkeypatch.setattr(post_task_mod, "get_asana_client", lambda: fake)

        post_task.fn(
            ExtractedTask(title="t", description="s", labels=["errand"]),
            drive_file_id="dup-id",
        )
        fake.find_or_create_tag.assert_not_called()
