"""Tests for flow.py — critical path: normalization, deduplication, failure paths, output shape."""

from __future__ import annotations

from unittest.mock import MagicMock, patch

import pytest

from notes_ingest_cog.flow import _coerce_session_type, process_transcript


# ── _coerce_session_type ──────────────────────────────────────────────────────

def test_coerce_session_type_valid() -> None:
    """Valid session types pass through unchanged."""
    for t in ("private_lesson", "class_taught", "class_attended", "workshop",
              "coaching_session", "other"):
        assert _coerce_session_type(t) == t


def test_coerce_session_type_legacy_group_class() -> None:
    """Legacy 'group_class' maps to 'class_attended'."""
    assert _coerce_session_type("group_class") == "class_attended"


def test_coerce_session_type_legacy_coaching() -> None:
    """Legacy 'coaching' maps to 'coaching_session'."""
    assert _coerce_session_type("coaching") == "coaching_session"


def test_coerce_session_type_unknown_falls_back() -> None:
    """Unknown values fall back to 'other'."""
    assert _coerce_session_type("something_weird") == "other"
    assert _coerce_session_type(None) == "other"
    assert _coerce_session_type("") == "other"


# ── process_transcript flow ───────────────────────────────────────────────────

_MINIMAL_NOTES = {
    "title": "Test lesson",
    "session_type": "private_lesson",
    "summary": "A test lesson about leading.",
}

_ENV_VARS = {
    "NOTES_INPUT_FOLDER_ID": "input-folder",
    "NOTES_PROCESSED_FOLDER_ID": "processed-folder",
    "KAIANO_API_BASE_URL": "http://localhost:8000",
    "KAIANO_API_INTERNAL_KEY": "test-key",
    "LLM_PROVIDER": "anthropic",
    "ANTHROPIC_API_KEY": "test-anthropic-key",
}


@pytest.fixture
def mock_env(monkeypatch: pytest.MonkeyPatch):
    for k, v in _ENV_VARS.items():
        monkeypatch.setenv(k, v)


@pytest.fixture
def mock_drive_text():
    """Returns a transcript long enough to pass the min_chars guard."""
    return "A" * 300


def test_process_transcript_skips_unsupported_mime(mock_env) -> None:
    """Unsupported MIME type returns skipped result without calling LLM."""
    result = process_transcript(
        file_id="file-123",
        file_name="something.pdf",
        mime_type="application/pdf",
    )
    assert result.get("skipped") is True
    assert "unsupported_mime_type" in result.get("reason", "")


def test_process_transcript_skips_short_transcript(mock_env) -> None:
    """Transcript below min_chars threshold returns skipped result."""
    with (
        patch("notes_ingest_cog.flow.GoogleAPI") as mock_gapi,
        patch("notes_ingest_cog.flow.NotesApiClient"),
    ):
        mock_g = MagicMock()
        mock_gapi.from_env.return_value = mock_g
        mock_g.drive.download_bytes.return_value = b"too short"

        result = process_transcript(
            file_id="file-123",
            file_name="short.txt",
            mime_type="text/plain",
        )

    assert result.get("skipped") is True
    assert result.get("reason") == "transcript_too_short"


def test_process_transcript_happy_path(mock_env, mock_drive_text) -> None:
    """Happy path returns transcript_id and note_id."""
    with (
        patch("notes_ingest_cog.flow.GoogleAPI") as mock_gapi,
        patch("notes_ingest_cog.flow.NotesApiClient") as mock_api_cls,
        patch("notes_ingest_cog.flow.build_llm") as mock_build_llm,
    ):
        # Drive mock
        mock_g = MagicMock()
        mock_gapi.from_env.return_value = mock_g
        mock_g.drive.download_bytes.return_value = mock_drive_text.encode()

        # API mock
        mock_api = MagicMock()
        mock_api_cls.return_value = mock_api
        mock_api.create_transcript.return_value = MagicMock(id="transcript-abc")
        mock_api.create_note.return_value = MagicMock(id="note-xyz")

        # LLM mock
        mock_llm = MagicMock()
        mock_build_llm.return_value = mock_llm
        mock_llm.generate_json.return_value = MagicMock(output_json=_MINIMAL_NOTES)

        result = process_transcript(
            file_id="file-123",
            file_name="plaud_lesson.txt",
            mime_type="text/plain",
        )

    assert result["transcript_id"] == "transcript-abc"
    assert result["note_id"] == "note-xyz"


def test_process_transcript_output_shape(mock_env, mock_drive_text) -> None:
    """Successful result always has transcript_id and note_id keys."""
    with (
        patch("notes_ingest_cog.flow.GoogleAPI") as mock_gapi,
        patch("notes_ingest_cog.flow.NotesApiClient") as mock_api_cls,
        patch("notes_ingest_cog.flow.build_llm") as mock_build_llm,
    ):
        mock_g = MagicMock()
        mock_gapi.from_env.return_value = mock_g
        mock_g.drive.download_bytes.return_value = mock_drive_text.encode()

        mock_api = MagicMock()
        mock_api_cls.return_value = mock_api
        mock_api.create_transcript.return_value = MagicMock(id="t-1")
        mock_api.create_note.return_value = MagicMock(id="n-1")

        mock_llm = MagicMock()
        mock_build_llm.return_value = mock_llm
        mock_llm.generate_json.return_value = MagicMock(output_json=_MINIMAL_NOTES)

        result = process_transcript(
            file_id="file-123",
            file_name="lesson.txt",
            mime_type="text/plain",
        )

    assert "transcript_id" in result
    assert "note_id" in result
    assert "skipped" not in result


def test_process_transcript_archives_file_on_success(mock_env, mock_drive_text) -> None:
    """Processed file is archived after successful completion."""
    with (
        patch("notes_ingest_cog.flow.GoogleAPI") as mock_gapi,
        patch("notes_ingest_cog.flow.NotesApiClient") as mock_api_cls,
        patch("notes_ingest_cog.flow.build_llm") as mock_build_llm,
        patch("notes_ingest_cog.flow.archive_file") as mock_archive,
    ):
        mock_g = MagicMock()
        mock_gapi.from_env.return_value = mock_g
        mock_g.drive.download_bytes.return_value = mock_drive_text.encode()

        mock_api = MagicMock()
        mock_api_cls.return_value = mock_api
        mock_api.create_transcript.return_value = MagicMock(id="t-1")
        mock_api.create_note.return_value = MagicMock(id="n-1")

        mock_llm = MagicMock()
        mock_build_llm.return_value = mock_llm
        mock_llm.generate_json.return_value = MagicMock(output_json=_MINIMAL_NOTES)

        process_transcript(
            file_id="file-123",
            file_name="lesson.txt",
            mime_type="text/plain",
        )

    mock_archive.assert_called_once()
