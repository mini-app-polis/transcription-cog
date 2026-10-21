"""Tests for request_task: the voice note as a task draft, and the handoff.

Covers TEST-002 (deduplication): the external id a note is requested under
is stable, so the task manager finds a note asked for twice.
"""

from __future__ import annotations

from datetime import date
from unittest.mock import MagicMock

import pytest
from mini_app_polis.api.contract import TaskCreateAccepted

from transcription_cog.voicenotes.models.extracted_task import ExtractedTask
from transcription_cog.voicenotes.tasks import request_task as request_task_mod
from transcription_cog.voicenotes.tasks.request_task import (
    audio_url,
    draft_for,
    external_id_for,
    request_for,
    request_task,
)


def _note(**overrides) -> ExtractedTask:
    base = {
        "title": "Send the floor trials report",
        "description": "Send it to Mark before the meeting.",
        "where": "dance",
        "who": "Mark",
        "when": "before Friday",
        "labels": ["follow-up"],
    }
    base.update(overrides)
    return ExtractedTask(**base)


@pytest.fixture(autouse=True)
def _fixed_today(monkeypatch: pytest.MonkeyPatch) -> date:
    today = date(2026, 10, 21)
    monkeypatch.setattr(request_task_mod, "_today", lambda: today)
    return today


class TestRequest:
    def test_keyed_by_source_and_audio_file(self) -> None:
        request = request_for(_note(), "drive-1")
        assert request.source == "voicenote"
        assert request.external_id == "voicenote.drive-1" == external_id_for("drive-1")
        # Voice-note ids are minted by the task manager, never sent.
        assert request.id is None


class TestDraft:
    def test_carries_what_the_note_said(self) -> None:
        draft = draft_for(_note(), "drive-1")
        assert draft.title == "Send the floor trials report"
        assert draft.where == "dance"
        assert draft.body == "Send it to Mark before the meeting."
        assert [(s.label, s.text) for s in draft.sections] == [
            ("Peers", "Mark"),
            ("Timeline", "before Friday"),
        ]
        assert [(x.text, x.url) for x in draft.links] == [
            ("Listen", "https://drive.google.com/file/d/drive-1/view")
        ]
        assert draft.labels == ["follow-up"]
        assert draft.needs_review is False

    def test_absent_fields_leave_no_section(self) -> None:
        draft = draft_for(_note(who=None, when=None, where=None), "drive-1")
        assert draft.sections == []
        assert draft.where is None

    def test_due_today_whatever_the_model_extracted(self, _fixed_today) -> None:
        draft = draft_for(_note(due_date=date(2027, 1, 1)), "drive-1")
        assert draft.due_on == _fixed_today

    def test_a_blank_title_is_sent_for_the_task_manager_to_name(self) -> None:
        draft = draft_for(_note(title="   ", needs_review=True, labels=[]), "drive-1")
        assert draft.title == ""
        assert draft.needs_review is True

    def test_long_model_output_is_cut_to_fit_rather_than_refused(self) -> None:
        draft = draft_for(
            _note(title="t" * 900, description="d" * 30_000, labels=["x" * 80]),
            "drive-1",
        )
        assert len(draft.title) == 500
        assert len(draft.body or "") == 20_000
        assert draft.labels == ["x" * 50]

    def test_the_audio_link_is_the_drive_viewer(self) -> None:
        assert audio_url("abc") == "https://drive.google.com/file/d/abc/view"


class TestHandoff:
    def test_sends_the_request_and_returns_its_message(self) -> None:
        api = MagicMock()
        api.request_task.return_value = TaskCreateAccepted(
            message_id="m-1", source="voicenote", external_id="voicenote.drive-1"
        )

        requested = request_task(_note(), "drive-1", api=api)

        assert requested.message_id == "m-1"
        (sent,) = api.request_task.call_args.args
        assert sent == request_for(_note(), "drive-1")

    def test_a_refused_request_raises_for_redelivery(self) -> None:
        api = MagicMock()
        api.request_task.side_effect = RuntimeError("502 dispatch_failed")

        with pytest.raises(RuntimeError, match="dispatch_failed"):
            request_task(_note(), "drive-1", api=api)

    def test_uses_this_cogs_own_key(self, monkeypatch: pytest.MonkeyPatch) -> None:
        seen: list[str] = []
        api = MagicMock()
        api.request_task.return_value = TaskCreateAccepted(
            message_id="m-1", source="voicenote", external_id="voicenote.drive-1"
        )

        def from_env(cls, machine_name=None):  # noqa: ARG001
            seen.append(machine_name)
            return api

        monkeypatch.setattr(
            request_task_mod.KaianoApiClient, "from_env", classmethod(from_env)
        )
        request_task(_note(), "drive-1")
        assert seen == ["transcription-cog"]
