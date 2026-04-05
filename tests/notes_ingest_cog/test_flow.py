"""Tests for flow.py — critical path: normalization, deduplication, failure paths, output shape."""

from __future__ import annotations

from unittest.mock import MagicMock, patch

import pytest

from notes_ingest_cog.flow import process_transcript

_ENV_VARS = {
    "NOTES_INPUT_FOLDER_ID": "input-folder",
    "NOTES_PROCESSED_FOLDER_ID": "processed-folder",
    "KAIANO_API_BASE_URL": "http://localhost:8000",
    "KAIANO_API_INTERNAL_KEY": "test-key",
    "LLM_PROVIDER": "anthropic",
    "ANTHROPIC_API_KEY": "test-anthropic-key",
}

_MINIMAL_NOTES = {
    "title": "Test lesson",
    "summary": "A test lesson about leading.",
}

_VALID_NAME_PRIVATE = "2026-04-01 Kaiano > Sarah - Connection.txt"
_VALID_NAME_GROUP = "2026-04-01 Kaiano > Swingesota.txt"


def _drive_item(file_id: str, filename: str, mime: str = "text/plain") -> MagicMock:
    """Build a Drive-like row. Do not pass ``name=`` to MagicMock — it is reserved."""
    m = MagicMock()
    m.id = file_id
    m.name = filename
    m.mime_type = mime
    return m


@pytest.fixture
def mock_env(monkeypatch: pytest.MonkeyPatch) -> None:
    for k, v in _ENV_VARS.items():
        monkeypatch.setenv(k, v)


@pytest.fixture
def mock_drive_text() -> str:
    """Returns a transcript long enough to pass the min_chars guard."""
    return "A" * 300


# ── process_transcript flow ───────────────────────────────────────────────────


def test_process_transcript_empty_folder(mock_env: None) -> None:
    """Empty folder returns zero counts with no errors."""
    with (
        patch("notes_ingest_cog.flow.GoogleAPI") as mock_gapi,
        patch("notes_ingest_cog.flow.NotesApiClient"),
    ):
        mock_g = MagicMock()
        mock_gapi.from_env.return_value = mock_g
        mock_g.drive.get_files_in_folder.return_value = []

        result = process_transcript()

    assert result["processed"] == 0
    assert result["skipped"] == 0
    assert result["files"] == []


def test_process_transcript_skips_short_transcript(mock_env: None) -> None:
    """Transcript below min_chars threshold is counted as skipped."""
    with (
        patch("notes_ingest_cog.flow.GoogleAPI") as mock_gapi,
        patch("notes_ingest_cog.flow.NotesApiClient"),
    ):
        mock_g = MagicMock()
        mock_gapi.from_env.return_value = mock_g
        mock_g.drive.get_files_in_folder.return_value = [
            _drive_item("file-1", _VALID_NAME_PRIVATE),
        ]
        mock_g.drive.download_bytes.return_value = b"too short"

        result = process_transcript()

    assert result["skipped"] == 1
    assert result["processed"] == 0


def test_process_transcript_skips_invalid_filename(mock_env: None) -> None:
    """Unparseable filename is skipped with reason invalid_filename."""
    with (
        patch("notes_ingest_cog.flow.GoogleAPI") as mock_gapi,
        patch("notes_ingest_cog.flow.NotesApiClient"),
        patch("notes_ingest_cog.flow.sentry_sdk.capture_message") as mock_sentry,
    ):
        mock_g = MagicMock()
        mock_gapi.from_env.return_value = mock_g
        mock_g.drive.get_files_in_folder.return_value = [
            _drive_item("file-1", "not-a-valid-name.txt"),
        ]

        result = process_transcript()

    assert result["skipped"] == 1
    assert result["processed"] == 0
    assert result["files"][0]["reason"] == "invalid_filename"
    mock_sentry.assert_called_once()


def test_process_transcript_skips_underscore_prefix(mock_env: None) -> None:
    """Leading underscore skips the file before Drive read."""
    with (
        patch("notes_ingest_cog.flow.GoogleAPI") as mock_gapi,
        patch("notes_ingest_cog.flow.NotesApiClient"),
    ):
        mock_g = MagicMock()
        mock_gapi.from_env.return_value = mock_g
        mock_g.drive.get_files_in_folder.return_value = [
            _drive_item("file-1", "_ignored.txt"),
        ]

        result = process_transcript()

    assert result["skipped"] == 1
    assert result["files"][0]["reason"] == "underscore_prefix"
    mock_g.drive.download_bytes.assert_not_called()


def test_process_transcript_happy_path(mock_env: None, mock_drive_text: str) -> None:
    """Happy path processes one file and returns correct counts."""
    with (
        patch("notes_ingest_cog.flow.GoogleAPI") as mock_gapi,
        patch("notes_ingest_cog.flow.NotesApiClient") as mock_api_cls,
        patch("notes_ingest_cog.flow.build_llm") as mock_build_llm,
    ):
        mock_g = MagicMock()
        mock_gapi.from_env.return_value = mock_g
        mock_g.drive.get_files_in_folder.return_value = [
            _drive_item("file-1", _VALID_NAME_PRIVATE),
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

    assert result["processed"] == 1
    assert result["skipped"] == 0
    assert result["files"][0]["transcript_id"] == "transcript-abc"
    assert result["files"][0]["note_id"] == "note-xyz"


def test_process_transcript_passes_parsed_metadata_to_note(
    mock_env: None, mock_drive_text: str
) -> None:
    """create_note receives session metadata and title from filename + LLM."""
    with (
        patch("notes_ingest_cog.flow.GoogleAPI") as mock_gapi,
        patch("notes_ingest_cog.flow.NotesApiClient") as mock_api_cls,
        patch("notes_ingest_cog.flow.build_llm") as mock_build_llm,
    ):
        mock_g = MagicMock()
        mock_gapi.from_env.return_value = mock_g
        mock_g.drive.get_files_in_folder.return_value = [
            _drive_item("file-1", _VALID_NAME_PRIVATE),
        ]
        mock_g.drive.download_bytes.return_value = mock_drive_text.encode()

        mock_api = MagicMock()
        mock_api_cls.return_value = mock_api
        mock_api.create_transcript.return_value = MagicMock(id="t-1")
        mock_api.create_note.return_value = MagicMock(id="n-1")

        mock_llm = MagicMock()
        mock_build_llm.return_value = mock_llm
        mock_llm.generate_json.return_value = MagicMock(
            output_json={**_MINIMAL_NOTES, "title": "LLM title"}
        )

        process_transcript()

    call = mock_api.create_note.call_args
    payload = call[0][0]
    assert payload.session_type == "private_lesson"
    assert payload.instructors == ["Kaiano"]
    assert payload.students == ["Sarah"]
    assert payload.organization == ""
    assert payload.session_date == "2026-04-01"
    assert payload.title == "Connection"


def test_process_transcript_output_shape(mock_env: None, mock_drive_text: str) -> None:
    """Result always contains processed, skipped, and files keys."""
    with (
        patch("notes_ingest_cog.flow.GoogleAPI") as mock_gapi,
        patch("notes_ingest_cog.flow.NotesApiClient") as mock_api_cls,
        patch("notes_ingest_cog.flow.build_llm") as mock_build_llm,
    ):
        mock_g = MagicMock()
        mock_gapi.from_env.return_value = mock_g
        mock_g.drive.get_files_in_folder.return_value = [
            _drive_item("file-1", _VALID_NAME_GROUP),
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

    assert "processed" in result
    assert "skipped" in result
    assert "files" in result


def test_process_transcript_archives_file_on_success(
    mock_env: None, mock_drive_text: str
) -> None:
    """Processed file is archived after successful completion."""
    with (
        patch("notes_ingest_cog.flow.GoogleAPI") as mock_gapi,
        patch("notes_ingest_cog.flow.NotesApiClient") as mock_api_cls,
        patch("notes_ingest_cog.flow.build_llm") as mock_build_llm,
        patch("notes_ingest_cog.flow.archive_file") as mock_archive,
    ):
        mock_g = MagicMock()
        mock_gapi.from_env.return_value = mock_g
        mock_g.drive.get_files_in_folder.return_value = [
            _drive_item("file-1", _VALID_NAME_PRIVATE),
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

    mock_archive.assert_called_once()


def test_process_transcript_continues_after_file_failure(
    mock_env: None, mock_drive_text: str
) -> None:
    """A failure on one file does not stop processing of subsequent files."""
    with (
        patch("notes_ingest_cog.flow.GoogleAPI") as mock_gapi,
        patch("notes_ingest_cog.flow.NotesApiClient") as mock_api_cls,
        patch("notes_ingest_cog.flow.build_llm") as mock_build_llm,
    ):
        mock_g = MagicMock()
        mock_gapi.from_env.return_value = mock_g
        mock_g.drive.get_files_in_folder.return_value = [
            _drive_item("file-1", "2026-04-01 Kaiano > A - B.txt"),
            _drive_item("file-2", "2026-04-01 Kaiano > C - D.txt"),
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

    assert result["processed"] == 1
    assert result["skipped"] == 1
