"""Tests for flow.py — one transcript file, end to end.

The flow processes the one file a queue message names. The folder is
still listed, because "is this file still in the input folder" is how a
second job for a file already archived knows to do nothing.
"""

from __future__ import annotations

from collections.abc import Iterator
from contextlib import contextmanager
from unittest.mock import MagicMock, patch

import pytest

from transcription_cog.flow import process_transcript

_ENV_VARS = {
    "NOTES_INPUT_FOLDER_ID": "input-folder",
    "NOTES_PROCESSED_FOLDER_ID": "processed-folder",
    "KAIANO_API_BASE_URL": "http://localhost:8000",
    "LLM_PROVIDER": "anthropic",
    "ANTHROPIC_API_KEY": "test-anthropic-key",
}

_MINIMAL_NOTES = {
    "title": "Test lesson",
    "summary": "A test lesson about leading.",
}

_VALID_FILENAME = "2026-04-01 Kaiano > Sarah - Connection.txt"
_VALID_GROUP_FILENAME = "2026-04-01 Kaiano > Swingesota.txt"
_INVALID_FILENAME = "random notes.txt"

_DOCX_MIME = "application/vnd.openxmlformats-officedocument.wordprocessingml.document"


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


class _Harness:
    """The flow's collaborators, mocked, with the run report captured."""

    def __init__(self) -> None:
        self.g = MagicMock()
        self.api = MagicMock()
        self.llm = MagicMock()
        self.post = MagicMock()
        self.api.create_transcript.return_value = MagicMock(id="transcript-abc")
        self.api.create_source.return_value = MagicMock(id="source-xyz")
        self.llm.generate_json.return_value = MagicMock(output_json=_MINIMAL_NOTES)

    def folder(self, *items: MagicMock) -> None:
        self.g.drive.get_files_in_folder.return_value = list(items)

    @property
    def severity(self) -> str:
        return self.post.call_args.args[1]

    @property
    def text(self) -> str:
        return self.post.call_args.kwargs["text"]


@pytest.fixture
def harness(mock_env: None, mock_drive_text: str) -> Iterator[_Harness]:
    h = _Harness()
    h.g.drive.download_bytes.return_value = mock_drive_text.encode()

    @contextmanager
    def _patched() -> Iterator[None]:
        with (
            patch("transcription_cog.flow.GoogleAPI") as gapi,
            patch("transcription_cog.flow.SubstrateApiClient", return_value=h.api),
            patch("transcription_cog.flow.build_llm", return_value=h.llm),
            patch("mini_app_polis.pipeline_status.post_run_finding", h.post),
        ):
            gapi.from_env.return_value = h.g
            yield

    with _patched():
        yield h


def test_happy_path(harness: _Harness) -> None:
    harness.folder(_drive_item("file-1", _VALID_FILENAME))

    result = process_transcript("file-1", run_id="msg-1")

    harness.api.create_transcript.assert_called_once()
    harness.api.create_source.assert_called_once()
    harness.llm.generate_json.assert_called_once()
    harness.g.drive.move_file.assert_called_once()
    assert result["processed"] == 1
    assert result["skipped"] == 0
    assert result["errors"] == 0
    assert result["files"][0]["transcript_id"] == "transcript-abc"
    assert result["files"][0]["source_id"] == "source-xyz"
    assert result["files"][0]["schema_valid"] is True

    harness.post.assert_called_once()
    assert harness.post.call_args.args[0] == "process-transcript"
    assert harness.severity == "SUCCESS"
    assert harness.post.call_args.kwargs["source"] == "flow_inline"


def test_the_report_carries_the_queue_message_id(harness: _Harness) -> None:
    """Not ``local-run``: the id watcher's log and the report share."""
    harness.folder(_drive_item("file-1", _VALID_FILENAME))

    process_transcript("file-1", run_id="msg-42")

    assert harness.post.call_args.kwargs["run_id"] == "msg-42"


def test_only_the_named_file_is_processed(harness: _Harness) -> None:
    """Other files in the folder are other jobs."""
    harness.folder(
        _drive_item("file-0", _VALID_GROUP_FILENAME),
        _drive_item("file-1", _VALID_FILENAME),
    )

    process_transcript("file-1", run_id="msg-1")

    harness.api.create_transcript.assert_called_once()
    payload = harness.api.create_transcript.call_args.args[0]
    assert payload.drive_file_id == "file-1"
    assert payload.source_filename == _VALID_FILENAME


def test_a_file_no_longer_in_the_folder_is_a_quiet_no_op(harness: _Harness) -> None:
    """An earlier job for this file archived it. The guard working is not news."""
    harness.folder(_drive_item("file-0", _VALID_GROUP_FILENAME))

    result = process_transcript("file-1", run_id="msg-1")

    harness.api.create_transcript.assert_not_called()
    harness.g.drive.move_file.assert_not_called()
    assert result["files"][0]["reason"] == "not_in_input_folder"
    harness.post.assert_called_once()
    assert harness.severity == "SUCCESS"
    assert harness.post.call_args.kwargs["notable"] is False


def test_invalid_filename_is_skipped_and_reported(harness: _Harness) -> None:
    harness.folder(_drive_item("file-1", _INVALID_FILENAME))

    result = process_transcript("file-1", run_id="msg-1")

    harness.api.create_transcript.assert_not_called()
    assert harness.severity == "WARN"
    assert result["skipped"] == 1
    assert result["files"][0]["reason"] == "invalid_filename"


def test_underscore_prefix_is_an_invalid_filename(harness: _Harness) -> None:
    harness.folder(_drive_item("file-1", "_2026-04-01 Kaiano > Sarah.txt"))

    result = process_transcript("file-1", run_id="msg-1")

    harness.api.create_transcript.assert_not_called()
    assert result["files"][0]["reason"] == "invalid_filename"


def test_a_short_transcript_is_skipped(harness: _Harness) -> None:
    harness.folder(_drive_item("file-1", _VALID_FILENAME))
    harness.g.drive.download_bytes.return_value = b"too short"

    result = process_transcript("file-1", run_id="msg-1")

    harness.api.create_transcript.assert_not_called()
    assert harness.severity == "WARN"
    assert result["files"][0]["reason"] == "transcript_too_short"


def test_parsed_metadata_reaches_the_source(harness: _Harness) -> None:
    harness.folder(_drive_item("file-1", _VALID_FILENAME))

    process_transcript("file-1", run_id="msg-1")

    payload = harness.api.create_source.call_args.args[0]
    assert payload.session_date == "2026-04-01"
    assert payload.instructors_raw == ["Kaiano"]
    assert payload.students_raw == ["Sarah"]
    assert payload.title == "Connection"


def test_an_already_processed_transcript_is_a_note(harness: _Harness) -> None:
    """The unique constraint on drive_file_id is the dedup guard (ADR-002)."""
    harness.folder(_drive_item("file-1", _VALID_FILENAME))
    harness.api.create_transcript.side_effect = RuntimeError(
        "duplicate key value violates unique constraint "
        '"uq_wcs_transcripts_drive_file_id"'
    )

    result = process_transcript("file-1", run_id="msg-1")

    harness.api.create_source.assert_not_called()
    assert result["files"][0]["reason"] == "already_processed"
    assert harness.severity == "SUCCESS"


def test_an_unsupported_file_type_is_reported(harness: _Harness) -> None:
    """A .docx stays in the inbox until a person moves it, so it is an issue."""
    harness.folder(_drive_item("file-doc", "meeting notes.docx", _DOCX_MIME))

    result = process_transcript("file-doc", run_id="msg-1")

    harness.api.create_transcript.assert_not_called()
    assert result["files"][0]["reason"] == "unsupported_file_type"
    assert harness.severity == "WARN"
    assert "unsupported_file_type" in harness.text
    assert "meeting notes.docx" in harness.text
    assert _DOCX_MIME in harness.text


def test_a_failure_is_reported_by_file_and_raised(harness: _Harness) -> None:
    """Raised, so the worker hands the message back; reported first, naming the file."""
    harness.folder(_drive_item("file-1", _VALID_FILENAME))

    with (
        patch(
            "transcription_cog.flow._process_one",
            side_effect=RuntimeError("drive read exploded"),
        ),
        pytest.raises(RuntimeError, match="drive read exploded"),
    ):
        process_transcript("file-1", run_id="msg-1")

    harness.post.assert_called_once()
    # The library grades it; what matters here is that it is not a success.
    assert harness.severity != "SUCCESS"
    assert "processing_failed" in harness.text
    assert _VALID_FILENAME in harness.text
    assert "RuntimeError" in harness.text
    assert harness.post.call_args.kwargs["run_id"] == "msg-1"


def test_a_failure_before_the_file_is_found_is_still_reported(
    harness: _Harness,
) -> None:
    """A Drive listing that fails is this run's failure too."""
    harness.g.drive.get_files_in_folder.side_effect = RuntimeError("drive is down")

    with pytest.raises(RuntimeError, match="drive is down"):
        process_transcript("file-1", run_id="msg-1")

    harness.post.assert_called_once()
    # The library grades it; what matters here is that it is not a success.
    assert harness.severity != "SUCCESS"
    assert "drive is down" in harness.text


def test_an_invalid_extraction_is_stored_and_flagged(harness: _Harness) -> None:
    harness.folder(_drive_item("file-1", _VALID_FILENAME))
    harness.llm.generate_json.return_value = MagicMock(output_json={"title": 7})

    result = process_transcript("file-1", run_id="msg-1")

    harness.api.create_source.assert_called_once()
    assert result["files"][0]["schema_valid"] is False
    assert "schema_invalid" in harness.text
