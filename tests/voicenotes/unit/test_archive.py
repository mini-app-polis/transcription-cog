"""Tests for archive task — YYYY-MM bucket computation and Drive flow."""

from __future__ import annotations

from datetime import UTC, datetime
from unittest.mock import MagicMock, patch

from notes_ingest_cog.voicenotes.clients import drive_client as drive_mod
from notes_ingest_cog.voicenotes.tasks import archive as archive_mod
from notes_ingest_cog.voicenotes.tasks.archive import _current_yyyy_mm, archive_audio


class TestYyyyMmBucket:
    """_current_yyyy_mm formats the current UTC date as YYYY-MM."""

    @patch("notes_ingest_cog.voicenotes.tasks.archive.datetime")
    def test_jan(self, mock_dt):
        """January renders with a zero-padded month."""
        mock_dt.now.return_value = datetime(2026, 1, 15, tzinfo=UTC)
        assert _current_yyyy_mm() == "2026-01"
        mock_dt.now.assert_called_once()

    @patch("notes_ingest_cog.voicenotes.tasks.archive.datetime")
    def test_dec(self, mock_dt):
        """December renders without a leading zero."""
        mock_dt.now.return_value = datetime(2026, 12, 31, tzinfo=UTC)
        assert _current_yyyy_mm() == "2026-12"
        mock_dt.now.assert_called_once()


class TestArchiveAudio:
    """archive_audio ensures processed/<YYYY-MM>/ exists then moves the file."""

    def test_creates_processed_and_yyyy_mm_then_moves(self, monkeypatch):
        """processed/ + YYYY-MM/ are ensure_subfolder'd, then file is moved."""
        fake = MagicMock(spec=drive_mod.DriveClient)
        # Two ensure_subfolder calls: processed/, then YYYY-MM/.
        fake.ensure_subfolder.side_effect = ["proc-id", "month-id"]
        # Patch the consumer's local reference, not the source module —
        # ``archive.py`` imported ``get_drive_client`` by name at module
        # load time.
        monkeypatch.setattr(archive_mod, "get_drive_client", lambda: fake)

        result = archive_audio.fn("file-1")

        assert result == "month-id"
        assert fake.ensure_subfolder.call_count == 2
        first_call = fake.ensure_subfolder.call_args_list[0]
        second_call = fake.ensure_subfolder.call_args_list[1]
        # First creates / finds processed/ under the inbox folder.
        assert first_call.args[1] == "processed"
        # Second creates / finds the YYYY-MM bucket under processed/.
        assert second_call.args[0] == "proc-id"
        # Move dispatched to the YYYY-MM folder ID.
        fake.move_file.assert_called_once_with("file-1", "month-id")
