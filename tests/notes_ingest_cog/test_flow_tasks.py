"""Direct unit tests for flow.py tasks — persistence and archival paths.

Resolves TEST-GAP-001: task_store_transcript, task_store_notes, and
task_archive_file had no direct coverage. These tests bypass Prefect's
task engine by calling the undecorated `.fn` attribute directly.
"""

from __future__ import annotations

from unittest.mock import MagicMock

import pytest

from notes_ingest_cog.filename_parser import ParsedFilename
from notes_ingest_cog.flow import (
    task_archive_file,
    task_post_evaluation,
    task_store_notes,
    task_store_transcript,
)


def _parsed() -> ParsedFilename:
    return ParsedFilename(
        recording_date="2026-04-01",
        instructors=["Kaiano"],
        students=["Sarah"],
        organization="",
        topic="Connection",
        session_type="private_lesson",
        raw_filename="2026-04-01 Kaiano > Sarah - Connection.txt",
    )


def _cfg() -> MagicMock:
    cfg = MagicMock()
    cfg.llm_model = "claude-sonnet-4-6"
    cfg.llm_provider = "anthropic"
    cfg.kaiano_api_base_url = "http://localhost:8000"
    cfg.kaiano_api_internal_key = "test-key"
    return cfg


# ── task_store_transcript ─────────────────────────────────────────────────────


def test_task_store_transcript_returns_id() -> None:
    api = MagicMock()
    api.create_transcript.return_value = MagicMock(id="t-1")

    result = task_store_transcript.fn(
        api,
        raw_text="lesson content " * 50,
        source_filename="2026-04-01 Kaiano > Sarah.txt",
        drive_file_id="drive-abc",
    )

    assert result == "t-1"
    api.create_transcript.assert_called_once()
    payload = api.create_transcript.call_args[0][0]
    assert payload.drive_file_id == "drive-abc"
    assert payload.source_filename == "2026-04-01 Kaiano > Sarah.txt"


def test_task_store_transcript_raises_on_duplicate() -> None:
    api = MagicMock()
    api.create_transcript.side_effect = Exception(
        "uq_wcs_transcripts_drive_file_id unique constraint"
    )

    with pytest.raises(Exception, match="uq_wcs_transcripts_drive_file_id"):
        task_store_transcript.fn(
            api,
            raw_text="content",
            source_filename="2026-04-01 Kaiano > Sarah.txt",
            drive_file_id="drive-abc",
        )


# ── task_store_notes ──────────────────────────────────────────────────────────


def test_task_store_notes_uses_topic_as_title() -> None:
    api = MagicMock()
    api.create_note.return_value = MagicMock(id="n-1")

    result = task_store_notes.fn(
        api,
        transcript_id="t-1",
        notes={"summary": "A lesson"},
        parsed=_parsed(),
        cfg=_cfg(),
    )

    assert result == "n-1"
    payload = api.create_note.call_args[0][0]
    assert payload.title == "Connection"
    assert payload.transcript_id == "t-1"
    assert payload.instructors == ["Kaiano"]
    assert payload.students == ["Sarah"]
    assert payload.session_type == "private_lesson"


def test_task_store_notes_falls_back_to_notes_title() -> None:
    api = MagicMock()
    api.create_note.return_value = MagicMock(id="n-1")
    parsed = ParsedFilename(
        recording_date="2026-04-01",
        instructors=["Kaiano"],
        students=["Sarah"],
        organization="",
        topic=None,
        session_type="private_lesson",
        raw_filename="2026-04-01 Kaiano > Sarah.txt",
    )

    task_store_notes.fn(
        api,
        transcript_id="t-1",
        notes={"title": "Extracted Title", "summary": "A lesson"},
        parsed=parsed,
        cfg=_cfg(),
    )

    payload = api.create_note.call_args[0][0]
    assert payload.title == "Extracted Title"


# ── task_archive_file ─────────────────────────────────────────────────────────


def test_task_archive_file_moves_to_processed_folder() -> None:
    g = MagicMock()

    task_archive_file.fn(
        g,
        file_id="drive-abc",
        processed_folder_id="processed-folder",
        name="2026-04-01 Kaiano > Sarah.txt",
    )

    g.drive.move_file.assert_called_once_with(
        "drive-abc", new_parent_id="processed-folder"
    )


# ── task_post_evaluation ──────────────────────────────────────────────────────


def test_task_post_evaluation_success_path() -> None:
    api = MagicMock()

    task_post_evaluation.fn(
        api,
        transcript_id="t-1",
        notes={"title": "X", "summary": "Y"},
        schema_valid=True,
        llm_model="claude-sonnet-4-6",
        llm_provider="anthropic",
    )

    api.post_evaluation.assert_called_once()
    kwargs = api.post_evaluation.call_args.kwargs
    assert kwargs["severity"] == "SUCCESS"
    assert kwargs["source"] == "notes-ingest-cog"
    assert kwargs["source_ref"] == "t-1"


def test_task_post_evaluation_warn_on_schema_invalid() -> None:
    api = MagicMock()

    task_post_evaluation.fn(
        api,
        transcript_id="t-1",
        notes={},
        schema_valid=False,
        llm_model="claude-sonnet-4-6",
        llm_provider="anthropic",
    )

    kwargs = api.post_evaluation.call_args.kwargs
    assert kwargs["severity"] == "WARN"


def test_task_post_evaluation_swallows_errors() -> None:
    """Evaluation posting failure must not bubble up — it's best-effort.

    Asserts the api client was actually called (not short-circuited elsewhere)
    and that the RuntimeError was caught rather than propagated. The test
    reaching this final assertion at all proves no exception was raised —
    satisfies TEST-011 mock verification and TEST-003 resilience (asserts the
    task did NOT raise to the caller).
    """
    api = MagicMock()
    api.post_evaluation.side_effect = RuntimeError("API down")

    task_post_evaluation.fn(
        api,
        transcript_id="t-1",
        notes={"title": "X"},
        schema_valid=True,
        llm_model="claude-sonnet-4-6",
        llm_provider="anthropic",
    )

    api.post_evaluation.assert_called_once()
