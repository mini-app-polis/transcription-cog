"""Tests for flow.py — critical path: normalization, deduplication, failure paths, output shape."""

from __future__ import annotations

from unittest.mock import MagicMock, patch

import pytest

from notes_ingest_cog.flow import process_transcript

_ENV_VARS = {
    "NOTES_INPUT_FOLDER_ID": "input-folder",
    "NOTES_PROCESSED_FOLDER_ID": "processed-folder",
    "KAIANO_API_BASE_URL": "http://localhost:8000",
    "LLM_PROVIDER": "anthropic",
    "ANTHROPIC_API_KEY": "test-anthropic-key",
    "TASK_RETRY_DELAY_SHORT": "0",
    "TASK_RETRY_DELAY_LONG": "0",
}

_MINIMAL_NOTES = {
    "title": "Test lesson",
    "summary": "A test lesson about leading.",
}

_VALID_FILENAME = "2026-04-01 Kaiano > Sarah - Connection.txt"
_VALID_GROUP_FILENAME = "2026-04-01 Kaiano > Swingesota.txt"
_INVALID_FILENAME = "random notes.txt"


def _drive_item(
    file_id: str, filename: str, mime_type: str = "text/plain"
) -> MagicMock:
    """Drive row mock; avoid ``MagicMock(name=...)`` — ``name`` is reserved on MagicMock."""
    m = MagicMock()
    m.id = file_id
    m.name = filename
    m.mime_type = mime_type
    return m


@pytest.fixture
def mock_env(monkeypatch: pytest.MonkeyPatch) -> None:
    for k, v in _ENV_VARS.items():
        monkeypatch.setenv(k, v)


@pytest.fixture
def mock_drive_text() -> str:
    return "A" * 300


def test_process_transcript_empty_folder(mock_env: None) -> None:
    with (
        patch("notes_ingest_cog.flow.GoogleAPI") as mock_gapi,
        patch("notes_ingest_cog.flow.NotesApiClient"),
    ):
        mock_g = MagicMock()
        mock_gapi.from_env.return_value = mock_g
        mock_g.drive.get_files_in_folder.return_value = []

        result = process_transcript()

    mock_gapi.from_env.assert_called_once()
    mock_g.drive.get_files_in_folder.assert_called_once()
    assert result["processed"] == 0
    assert result["skipped"] == 0
    assert result["files"] == []


def test_process_transcript_skips_invalid_filename(mock_env: None) -> None:
    with (
        patch("notes_ingest_cog.flow.GoogleAPI") as mock_gapi,
        patch("notes_ingest_cog.flow.NotesApiClient") as mock_api_cls,
    ):
        mock_g = MagicMock()
        mock_gapi.from_env.return_value = mock_g
        mock_g.drive.get_files_in_folder.return_value = [
            _drive_item("file-1", _INVALID_FILENAME),
        ]
        mock_api = MagicMock()
        mock_api_cls.return_value = mock_api

        result = process_transcript()

    mock_g.drive.get_files_in_folder.assert_called_once()
    mock_api.create_transcript.assert_not_called()
    assert result["skipped"] == 1
    assert result["processed"] == 0
    assert result["files"][0]["reason"] == "invalid_filename"


def test_process_transcript_skips_underscore_prefix(mock_env: None) -> None:
    with (
        patch("notes_ingest_cog.flow.GoogleAPI") as mock_gapi,
        patch("notes_ingest_cog.flow.NotesApiClient") as mock_api_cls,
    ):
        mock_g = MagicMock()
        mock_gapi.from_env.return_value = mock_g
        mock_g.drive.get_files_in_folder.return_value = [
            _drive_item("file-1", "_2026-04-01 Kaiano > Sarah.txt"),
        ]
        mock_api = MagicMock()
        mock_api_cls.return_value = mock_api

        result = process_transcript()

    mock_g.drive.get_files_in_folder.assert_called_once()
    mock_api.create_transcript.assert_not_called()
    assert result["skipped"] == 1
    assert result["files"][0]["reason"] == "invalid_filename"


def test_process_transcript_skips_short_transcript(mock_env: None) -> None:
    with (
        patch("notes_ingest_cog.flow.GoogleAPI") as mock_gapi,
        patch("notes_ingest_cog.flow.NotesApiClient") as mock_api_cls,
    ):
        mock_g = MagicMock()
        mock_gapi.from_env.return_value = mock_g
        mock_g.drive.get_files_in_folder.return_value = [
            _drive_item("file-1", _VALID_FILENAME),
        ]
        mock_g.drive.download_bytes.return_value = b"too short"
        mock_api = MagicMock()
        mock_api_cls.return_value = mock_api

        result = process_transcript()

    mock_g.drive.get_files_in_folder.assert_called_once()
    mock_api.create_transcript.assert_not_called()
    assert result["skipped"] == 1
    assert result["files"][0]["reason"] == "transcript_too_short"


def test_process_transcript_happy_path(mock_env: None, mock_drive_text: str) -> None:
    with (
        patch("notes_ingest_cog.flow.GoogleAPI") as mock_gapi,
        patch("notes_ingest_cog.flow.NotesApiClient") as mock_api_cls,
        patch("notes_ingest_cog.flow.build_llm") as mock_build_llm,
    ):
        mock_g = MagicMock()
        mock_gapi.from_env.return_value = mock_g
        mock_g.drive.get_files_in_folder.return_value = [
            _drive_item("file-1", _VALID_FILENAME),
        ]
        mock_g.drive.download_bytes.return_value = mock_drive_text.encode()

        mock_api = MagicMock()
        mock_api_cls.return_value = mock_api
        mock_api.create_transcript.return_value = MagicMock(id="transcript-abc")
        mock_api.create_note.return_value = MagicMock(id="note-xyz")

        mock_llm = MagicMock()
        mock_build_llm.return_value = mock_llm
        mock_llm.generate_json.return_value = MagicMock(output_json=_MINIMAL_NOTES)

        result = process_transcript()

    mock_api.create_transcript.assert_called_once()
    mock_api.create_note.assert_called_once()
    mock_llm.generate_json.assert_called_once()
    # PIPE-009 / PIPE-011: evaluation finding posted for every processed file
    # (Evaluation posting is best-effort; mock the client at the import site
    # if you want to assert against it. Otherwise it silently no-ops on the
    # test API URL and is caught by task_post_evaluation's inner try/except.)
    assert result["processed"] == 1
    assert result["skipped"] == 0
    assert result["files"][0]["transcript_id"] == "transcript-abc"
    assert result["files"][0]["note_id"] == "note-xyz"


def test_process_transcript_passes_parsed_metadata_to_note(
    mock_env: None, mock_drive_text: str
) -> None:
    with (
        patch("notes_ingest_cog.flow.GoogleAPI") as mock_gapi,
        patch("notes_ingest_cog.flow.NotesApiClient") as mock_api_cls,
        patch("notes_ingest_cog.flow.build_llm") as mock_build_llm,
    ):
        mock_g = MagicMock()
        mock_gapi.from_env.return_value = mock_g
        mock_g.drive.get_files_in_folder.return_value = [
            _drive_item("file-1", _VALID_FILENAME),
        ]
        mock_g.drive.download_bytes.return_value = mock_drive_text.encode()

        mock_api = MagicMock()
        mock_api_cls.return_value = mock_api
        mock_api.create_transcript.return_value = MagicMock(id="t-1")
        mock_api.create_note.return_value = MagicMock(id="n-1")

        mock_llm = MagicMock()
        mock_build_llm.return_value = mock_llm
        mock_llm.generate_json.return_value = MagicMock(output_json=_MINIMAL_NOTES)

        process_transcript()

    mock_api.create_note.assert_called_once()
    # PIPE-009 / PIPE-011: evaluation finding posted for every processed file
    # (Evaluation posting is best-effort; mock the client at the import site
    # if you want to assert against it. Otherwise it silently no-ops on the
    # test API URL and is caught by task_post_evaluation's inner try/except.)
    call_kwargs = mock_api.create_note.call_args[0][0]
    assert call_kwargs.session_type == "private_lesson"
    assert call_kwargs.instructors == ["Kaiano"]
    assert call_kwargs.students == ["Sarah"]
    assert call_kwargs.session_date == "2026-04-01"
    assert call_kwargs.title == "Connection"


def test_process_transcript_output_shape(mock_env: None, mock_drive_text: str) -> None:
    with (
        patch("notes_ingest_cog.flow.GoogleAPI") as mock_gapi,
        patch("notes_ingest_cog.flow.NotesApiClient") as mock_api_cls,
        patch("notes_ingest_cog.flow.build_llm") as mock_build_llm,
    ):
        mock_g = MagicMock()
        mock_gapi.from_env.return_value = mock_g
        mock_g.drive.get_files_in_folder.return_value = [
            _drive_item("file-1", _VALID_FILENAME),
        ]
        mock_g.drive.download_bytes.return_value = mock_drive_text.encode()

        mock_api = MagicMock()
        mock_api_cls.return_value = mock_api
        mock_api.create_transcript.return_value = MagicMock(id="t-1")
        mock_api.create_note.return_value = MagicMock(id="n-1")

        mock_llm = MagicMock()
        mock_build_llm.return_value = mock_llm
        mock_llm.generate_json.return_value = MagicMock(output_json=_MINIMAL_NOTES)

        result = process_transcript()

    mock_api.create_transcript.assert_called_once()
    mock_api.create_note.assert_called_once()
    # PIPE-009 / PIPE-011: evaluation finding posted for every processed file
    # (Evaluation posting is best-effort; mock the client at the import site
    # if you want to assert against it. Otherwise it silently no-ops on the
    # test API URL and is caught by task_post_evaluation's inner try/except.)
    assert "processed" in result
    assert "skipped" in result
    assert "files" in result


def test_process_transcript_skips_already_processed(
    mock_env: None, mock_drive_text: str
) -> None:
    with (
        patch("notes_ingest_cog.flow.GoogleAPI") as mock_gapi,
        patch("notes_ingest_cog.flow.NotesApiClient") as mock_api_cls,
    ):
        mock_g = MagicMock()
        mock_gapi.from_env.return_value = mock_g
        mock_g.drive.get_files_in_folder.return_value = [
            _drive_item("file-1", _VALID_FILENAME),
        ]
        mock_g.drive.download_bytes.return_value = mock_drive_text.encode()

        mock_api = MagicMock()
        mock_api_cls.return_value = mock_api
        mock_api.create_transcript.side_effect = Exception(
            "uq_wcs_transcripts_drive_file_id unique constraint"
        )

        result = process_transcript()

    # task_store_transcript sets retries=2, so Prefect attempts the task three times
    # before the exception reaches the flow handler (session env defaults do not
    # override an explicit retries= on the decorator).
    assert mock_api.create_transcript.call_count == 3
    assert result["skipped"] == 1
    assert result["files"][0]["reason"] == "already_processed"


def test_process_transcript_continues_after_failure(
    mock_env: None, mock_drive_text: str
) -> None:
    with (
        patch("notes_ingest_cog.flow.GoogleAPI") as mock_gapi,
        patch("notes_ingest_cog.flow.NotesApiClient") as mock_api_cls,
        patch("notes_ingest_cog.flow.build_llm") as mock_build_llm,
    ):
        mock_g = MagicMock()
        mock_gapi.from_env.return_value = mock_g
        mock_g.drive.get_files_in_folder.return_value = [
            _drive_item("file-1", _VALID_FILENAME),
            _drive_item("file-2", _VALID_GROUP_FILENAME),
        ]
        mock_g.drive.download_bytes.side_effect = [
            RuntimeError("drive error"),
            mock_drive_text.encode(),
        ]

        mock_api = MagicMock()
        mock_api_cls.return_value = mock_api
        mock_api.create_transcript.return_value = MagicMock(id="t-1")
        mock_api.create_note.return_value = MagicMock(id="n-1")

        mock_llm = MagicMock()
        mock_build_llm.return_value = mock_llm
        mock_llm.generate_json.return_value = MagicMock(output_json=_MINIMAL_NOTES)

        result = process_transcript()

    mock_api.create_transcript.assert_called_once()
    mock_api.create_note.assert_called_once()
    mock_llm.generate_json.assert_called_once()
    assert result["processed"] == 1
    assert result["skipped"] == 1


def test_process_transcript_posts_evaluation(
    mock_env: None, mock_drive_text: str
) -> None:
    with (
        patch("notes_ingest_cog.flow.GoogleAPI") as mock_gapi,
        patch("notes_ingest_cog.flow.NotesApiClient") as mock_api_cls,
        patch("notes_ingest_cog.flow.build_llm") as mock_build_llm,
    ):
        mock_g = MagicMock()
        mock_gapi.from_env.return_value = mock_g
        mock_g.drive.get_files_in_folder.return_value = [
            _drive_item("file-1", _VALID_FILENAME),
        ]
        mock_g.drive.download_bytes.return_value = mock_drive_text.encode()

        mock_api = MagicMock()
        mock_api_cls.return_value = mock_api
        mock_api.create_transcript.return_value = MagicMock(id="t-1")
        mock_api.create_note.return_value = MagicMock(id="n-1")

        mock_llm = MagicMock()
        mock_build_llm.return_value = mock_llm
        mock_llm.generate_json.return_value = MagicMock(output_json=_MINIMAL_NOTES)

        process_transcript()

    mock_api.post_evaluation.assert_called_once()
    call_kwargs = mock_api.post_evaluation.call_args.kwargs
    assert call_kwargs["source"] == "notes-ingest-cog"
    assert call_kwargs["source_ref"] == "t-1"
    assert call_kwargs["dimension"] == "llm_output_quality"
