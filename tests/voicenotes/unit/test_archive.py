"""Tests for archive task — YYYY-MM-DD bucket computation and Drive flow.

Buckets are per processing-day, not per month — see archive.py
docstring for the rationale. The previous monthly layout
(``processed/YYYY-MM/``) collapsed every same-month run into one
folder, which was opaque for triage. The legacy monthly folders
still exist in production and are still drained by the cleanup
flow, but new archives go under daily folders.
"""

from __future__ import annotations

from datetime import UTC, datetime
from unittest.mock import MagicMock, patch

from transcription_cog.voicenotes.clients import drive_client as drive_mod
from transcription_cog.voicenotes.tasks import archive as archive_mod
from transcription_cog.voicenotes.tasks.archive import (
    _current_yyyy_mm_dd,
    archive_audio,
)


class TestYyyyMmDdBucket:
    """_current_yyyy_mm_dd formats the current UTC date as YYYY-MM-DD."""

    @patch("transcription_cog.voicenotes.tasks.archive.datetime")
    def test_jan(self, mock_dt):
        """January renders with zero-padded month and day."""
        mock_dt.now.return_value = datetime(2026, 1, 5, tzinfo=UTC)
        assert _current_yyyy_mm_dd() == "2026-01-05"
        mock_dt.now.assert_called_once()

    @patch("transcription_cog.voicenotes.tasks.archive.datetime")
    def test_dec(self, mock_dt):
        """December renders without leading zeros on month or day."""
        mock_dt.now.return_value = datetime(2026, 12, 31, tzinfo=UTC)
        assert _current_yyyy_mm_dd() == "2026-12-31"
        mock_dt.now.assert_called_once()

    @patch("transcription_cog.voicenotes.tasks.archive.datetime")
    def test_distinct_days_in_same_month_get_distinct_buckets(self, mock_dt):
        """Two same-month runs on different days land in different folders.

        Regression for the previous monthly-bucket bug, where every
        voicenote processed in May went to the single ``2026-05/``
        folder. The new layout must give each day its own folder so
        operators can see a day's batch at a glance.
        """
        mock_dt.now.return_value = datetime(2026, 5, 27, tzinfo=UTC)
        bucket_a = _current_yyyy_mm_dd()
        mock_dt.now.return_value = datetime(2026, 5, 28, tzinfo=UTC)
        bucket_b = _current_yyyy_mm_dd()
        assert bucket_a == "2026-05-27"
        assert bucket_b == "2026-05-28"
        assert bucket_a != bucket_b
        assert mock_dt.now.call_count == 2


class TestArchiveAudio:
    """archive_audio ensures processed/<YYYY-MM-DD>/ exists then moves the file."""

    def test_creates_processed_and_yyyy_mm_dd_then_moves(self, monkeypatch):
        """processed/ + YYYY-MM-DD/ are ensure_subfolder'd, then file is moved."""
        fake = MagicMock(spec=drive_mod.DriveClient)
        # Two ensure_subfolder calls: processed/, then YYYY-MM-DD/.
        fake.ensure_subfolder.side_effect = ["proc-id", "day-id"]
        # Patch the consumer's local reference, not the source module —
        # ``archive.py`` imported ``get_drive_client`` by name at module
        # load time.
        monkeypatch.setattr(archive_mod, "get_drive_client", lambda: fake)

        result = archive_audio.fn("file-1")

        assert result == "day-id"
        assert fake.ensure_subfolder.call_count == 2
        first_call = fake.ensure_subfolder.call_args_list[0]
        second_call = fake.ensure_subfolder.call_args_list[1]
        # First creates / finds processed/ under the inbox folder.
        assert first_call.args[1] == "processed"
        # Second creates / finds the YYYY-MM-DD bucket under processed/.
        assert second_call.args[0] == "proc-id"
        # The bucket name follows the YYYY-MM-DD pattern (10 chars,
        # two dashes). Don't pin a specific date — wall-clock time
        # advances between when the test was written and when it runs.
        bucket_name = second_call.args[1]
        assert len(bucket_name) == 10
        assert bucket_name[4] == "-" and bucket_name[7] == "-"
        # Move dispatched to the YYYY-MM-DD folder ID.
        fake.move_file.assert_called_once_with("file-1", "day-id")
