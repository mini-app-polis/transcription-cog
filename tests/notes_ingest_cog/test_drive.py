"""Tests for drive.py — normalization, failure paths, output shape."""

from __future__ import annotations

from unittest.mock import MagicMock

import pytest

from notes_ingest_cog.drive import infer_source_type, read_transcript_text

DOC_MIME = "application/vnd.google-apps.document"
TXT_MIME = "text/plain"


# ── infer_source_type ─────────────────────────────────────────────────────────

def test_infer_source_type_plaud() -> None:
    assert infer_source_type("plaud_recording_2024.txt") == "plaud"


def test_infer_source_type_otter() -> None:
    assert infer_source_type("Otter_Session_Notes.txt") == "otter"


def test_infer_source_type_zoom() -> None:
    assert infer_source_type("zoom_transcript_001.txt") == "zoom"


def test_infer_source_type_meet() -> None:
    assert infer_source_type("google_meet_recording.txt") == "google_meet"


def test_infer_source_type_unknown() -> None:
    assert infer_source_type("my_random_file.txt") == "unknown"


# ── read_transcript_text ──────────────────────────────────────────────────────

def test_read_transcript_google_doc() -> None:
    """Google Doc calls export_google_doc_as_text."""
    g = MagicMock()
    g.drive.export_google_doc_as_text.return_value = "lesson notes"

    result = read_transcript_text(g, "file-id", DOC_MIME)

    assert result == "lesson notes"
    g.drive.export_google_doc_as_text.assert_called_once_with("file-id")


def test_read_transcript_txt_via_download_bytes() -> None:
    """Plain text file is read via download_bytes when available."""
    g = MagicMock()
    g.drive.download_bytes.return_value = b"raw transcript text"

    result = read_transcript_text(g, "file-id", TXT_MIME)

    assert result == "raw transcript text"


def test_read_transcript_txt_fallback_to_service() -> None:
    """Falls back to raw Drive service when no download_bytes method exists."""
    g = MagicMock(spec=[])  # no attributes by default
    g.drive = MagicMock(spec=["service"])
    g.drive.service.files.return_value.get_media.return_value.execute.return_value = (
        b"fallback content"
    )

    result = read_transcript_text(g, "file-id", TXT_MIME)

    assert result == "fallback content"


def test_read_transcript_unsupported_mime_raises() -> None:
    """Unsupported MIME type raises ValueError."""
    g = MagicMock()
    with pytest.raises(ValueError, match="Unsupported mime type"):
        read_transcript_text(g, "file-id", "application/pdf")


def test_read_transcript_txt_no_download_method_raises() -> None:
    """TypeError raised when no bytes download method is available."""
    g = MagicMock()
    # Remove all known download method names and service
    for attr in ("download_bytes", "download_file_bytes", "download_file_as_bytes",
                 "get_file_bytes", "service"):
        if hasattr(g.drive, attr):
            delattr(type(g.drive), attr)
    g.drive = MagicMock(spec=[])  # empty spec — no download methods, no service

    with pytest.raises(TypeError, match="does not expose a supported bytes download method"):
        read_transcript_text(g, "file-id", TXT_MIME)
