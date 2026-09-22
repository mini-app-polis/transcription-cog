"""Direct unit tests for flow.py tasks — persistence and archival paths.

Resolves TEST-GAP-001: task_store_transcript, task_store_source, and
task_archive_file had no direct coverage. They are plain functions now,
so these call them directly.
"""

from __future__ import annotations

from unittest.mock import MagicMock, patch

import pytest
from mini_app_polis.llm.errors import LLMTruncationError

from transcription_cog.filename_parser import ParsedFilename
from transcription_cog.flow import (
    task_archive_file,
    task_call_llm,
    task_store_source,
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

    result = task_store_transcript(
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
        task_store_transcript(
            api,
            raw_text="content",
            source_filename="2026-04-01 Kaiano > Sarah.txt",
            drive_file_id="drive-abc",
        )


# ── task_call_llm ─────────────────────────────────────────────────────────────


def test_task_call_llm_truncation_error_is_propagated_with_clear_log(
    caplog: pytest.LogCaptureFixture,
) -> None:
    cfg = _cfg()
    parsed = _parsed()
    truncation_error = LLMTruncationError(
        "Response truncated at max_tokens=16384 (stop_reason=max_tokens)"
    )

    with (
        patch("transcription_cog.flow.build_llm") as mock_build_llm,
        caplog.at_level("ERROR"),
    ):
        mock_llm = MagicMock()
        mock_build_llm.return_value = mock_llm
        mock_llm.generate_json.side_effect = truncation_error

        with pytest.raises(LLMTruncationError, match="truncated at max_tokens"):
            task_call_llm(
                cfg,
                transcript_text="dense workshop transcript " * 200,
                parsed=parsed,
            )

    assert any("truncated" in record.message.lower() for record in caplog.records)
    mock_llm.generate_json.assert_called_once()


# ── task_store_source ─────────────────────────────────────────────────────────


def test_task_store_source_uses_topic_as_title() -> None:
    api = MagicMock()
    api.create_source.return_value = MagicMock(id="src-1")

    result = task_store_source(
        api,
        transcript_id="t-1",
        extraction={"summary": "A lesson"},
        parsed=_parsed(),
        cfg=_cfg(),
    )

    assert result == "src-1"
    payload = api.create_source.call_args[0][0]
    assert payload.title == "Connection"
    assert payload.transcript_id == "t-1"
    assert payload.instructors_raw == ["Kaiano"]
    assert payload.students_raw == ["Sarah"]
    assert payload.session_type == "private_lesson"
    assert payload.extractor_model == "claude-sonnet-4-6"
    assert payload.extractor_provider == "anthropic"
    assert payload.prompt_version == "2.4.0"
    assert payload.raw_output == {"summary": "A lesson"}


def test_task_store_source_falls_back_to_extraction_title() -> None:
    api = MagicMock()
    api.create_source.return_value = MagicMock(id="src-1")
    parsed = ParsedFilename(
        recording_date="2026-04-01",
        instructors=["Kaiano"],
        students=["Sarah"],
        organization="",
        topic=None,
        session_type="private_lesson",
        raw_filename="2026-04-01 Kaiano > Sarah.txt",
    )

    task_store_source(
        api,
        transcript_id="t-1",
        extraction={"title": "Extracted Title", "summary": "A lesson"},
        parsed=parsed,
        cfg=_cfg(),
    )

    payload = api.create_source.call_args[0][0]
    assert payload.title == "Extracted Title"


# ── task_archive_file ─────────────────────────────────────────────────────────


def test_task_archive_file_moves_to_processed_folder() -> None:
    g = MagicMock()
    g.drive.service.files.return_value.get.return_value.execute.return_value = {
        "parents": ["input-folder"],
    }

    task_archive_file(
        g,
        file_id="drive-abc",
        processed_folder_id="processed-folder",
        name="2026-04-01 Kaiano > Sarah.txt",
    )

    g.drive.move_file.assert_called_once_with(
        "drive-abc", new_parent_id="processed-folder"
    )
