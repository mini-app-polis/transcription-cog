"""Direct unit tests for flow.py tasks — persistence and archival paths.

Resolves TEST-GAP-001: task_store_transcript, task_store_notes, and
task_archive_file had no direct coverage. These tests bypass Prefect's
task engine by calling the undecorated `.fn` attribute directly.
"""

from __future__ import annotations

from unittest.mock import MagicMock, patch

import pytest

from notes_ingest_cog.filename_parser import ParsedFilename
from notes_ingest_cog.flow import (
    task_archive_file,
    task_post_run_evaluation,
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


# ── task_post_run_evaluation ───────────────────────────────────────────────────


def test_task_post_run_evaluation_empty_batch_success() -> None:
    api = MagicMock()

    with patch("notes_ingest_cog.flow._get_logger", return_value=MagicMock()):
        task_post_run_evaluation.fn(
            api,
            processed=0,
            skipped=0,
            results=[],
            errors=0,
        )

    api.post_run_evaluation.assert_called_once()
    kwargs = api.post_run_evaluation.call_args.kwargs
    assert kwargs["severity"] == "SUCCESS"
    assert kwargs["repo"] == "notes-ingest-cog"
    assert kwargs["dimension"] == "pipeline_consistency"
    assert "no files to process" in kwargs["finding"].lower()


def test_task_post_run_evaluation_all_clean_success() -> None:
    api = MagicMock()

    with patch("notes_ingest_cog.flow._get_logger", return_value=MagicMock()):
        task_post_run_evaluation.fn(
            api,
            processed=2,
            skipped=0,
            results=[
                {"schema_valid": True, "file": "a.txt"},
                {"schema_valid": True, "file": "b.txt"},
            ],
            errors=0,
        )

    kwargs = api.post_run_evaluation.call_args.kwargs
    assert kwargs["severity"] == "SUCCESS"
    assert "processed=2" in kwargs["finding"]


def test_task_post_run_evaluation_schema_invalid_warns() -> None:
    api = MagicMock()

    with patch("notes_ingest_cog.flow._get_logger", return_value=MagicMock()):
        task_post_run_evaluation.fn(
            api,
            processed=1,
            skipped=0,
            results=[{"schema_valid": False, "file": "a.txt"}],
            errors=0,
        )

    assert api.post_run_evaluation.call_args.kwargs["severity"] == "WARN"
    assert "schema_invalid=1" in api.post_run_evaluation.call_args.kwargs["finding"]


def test_task_post_run_evaluation_data_skip_warns() -> None:
    api = MagicMock()

    with patch("notes_ingest_cog.flow._get_logger", return_value=MagicMock()):
        task_post_run_evaluation.fn(
            api,
            processed=0,
            skipped=1,
            results=[{"skipped": True, "reason": "invalid_filename", "file": "x.txt"}],
            errors=0,
        )

    kwargs = api.post_run_evaluation.call_args.kwargs
    assert kwargs["severity"] == "WARN"
    assert "data_skips=1" in kwargs["finding"]


def test_task_post_run_evaluation_transcript_too_short_warns() -> None:
    api = MagicMock()

    with patch("notes_ingest_cog.flow._get_logger", return_value=MagicMock()):
        task_post_run_evaluation.fn(
            api,
            processed=0,
            skipped=1,
            results=[
                {"skipped": True, "reason": "transcript_too_short", "file": "x.txt"}
            ],
            errors=0,
        )

    assert api.post_run_evaluation.call_args.kwargs["severity"] == "WARN"


def test_task_post_run_evaluation_already_processed_only_success() -> None:
    api = MagicMock()

    with patch("notes_ingest_cog.flow._get_logger", return_value=MagicMock()):
        task_post_run_evaluation.fn(
            api,
            processed=0,
            skipped=1,
            results=[{"skipped": True, "reason": "already_processed", "file": "a.txt"}],
            errors=0,
        )

    kwargs = api.post_run_evaluation.call_args.kwargs
    assert kwargs["severity"] == "SUCCESS"
    assert "already_processed=1" in kwargs["finding"]


def test_task_post_run_evaluation_errors_warn() -> None:
    api = MagicMock()

    with patch("notes_ingest_cog.flow._get_logger", return_value=MagicMock()):
        task_post_run_evaluation.fn(
            api,
            processed=0,
            skipped=0,
            results=[],
            errors=2,
        )

    kwargs = api.post_run_evaluation.call_args.kwargs
    assert kwargs["severity"] == "WARN"
    assert "errors=2" in kwargs["finding"]


def test_task_post_run_evaluation_swallows_post_errors() -> None:
    """Run evaluation POST failure must not bubble up — it's best-effort."""
    api = MagicMock()
    api.post_run_evaluation.side_effect = RuntimeError("API down")

    with patch("notes_ingest_cog.flow._get_logger", return_value=MagicMock()):
        task_post_run_evaluation.fn(
            api,
            processed=1,
            skipped=0,
            results=[{"schema_valid": True}],
            errors=0,
        )

    api.post_run_evaluation.assert_called_once()
