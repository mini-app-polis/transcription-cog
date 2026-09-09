"""Tests for the retention sweep — counting, filtering, and honest reporting.

There was no test module here, which is how ``deleted=0 failed=38`` came
to be logged as ``voicenotes.cleanup.success`` in production. The
assertions below are mostly about what the sweep *says* it did.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from types import SimpleNamespace
from unittest.mock import MagicMock

import pytest

from transcription_cog.voicenotes.flows import cleanup as cleanup_mod
from transcription_cog.voicenotes.flows.cleanup import voicenotes_cleanup

_FOLDER_MIME = "application/vnd.google-apps.folder"


def _bucket_name(days_ago: int) -> str:
    """Name of the per-day bucket archived ``days_ago`` days ago."""
    return (datetime.now(UTC).date() - timedelta(days=days_ago)).strftime("%Y-%m-%d")


def _folder(file_id: str, name: str | None = None):
    # Default to a bucket comfortably outside the 14-day window so tests
    # that care about counting, not dating, drain as expected.
    return SimpleNamespace(
        id=file_id, name=name or _bucket_name(90), mime_type=_FOLDER_MIME
    )


def _file(file_id: str, name: str = "note.m4a"):
    return SimpleNamespace(id=file_id, name=name, mime_type="audio/mp4")


def _drive(*, children, old_files, delete_side_effect=None):
    """Build a DriveClient stub for one processed/ root.

    ``list_files`` answers the processed/ root with ``children`` and any
    bucket with ``old_files`` — dated buckets are drained whole through
    ``list_files``, undated ones through ``list_files_older_than``.
    """
    drive = MagicMock()
    drive.ensure_subfolder.return_value = "processed-root"

    def _list_files(folder_id):
        return children if folder_id == "processed-root" else old_files

    drive.list_files.side_effect = _list_files
    drive.list_files_older_than.return_value = old_files
    if delete_side_effect is not None:
        drive.delete_file.side_effect = delete_side_effect
    return drive


@pytest.fixture
def patch_drive(monkeypatch):
    def _apply(drive):
        monkeypatch.setattr(cleanup_mod, "get_drive_client", lambda: drive)
        return drive

    return _apply


class TestCounting:
    def test_idle_sweep_reports_nothing_attempted(self, patch_drive):
        """Nothing past retention is not the same as nothing working."""
        patch_drive(_drive(children=[_folder("d1")], old_files=[]))
        summary = voicenotes_cleanup.fn()
        assert summary["attempted"] == 0
        assert summary["deleted"] == 0
        assert summary["failed"] == 0
        assert summary["date_folders_scanned"] == 1

    def test_stray_file_in_processed_root_is_not_counted_as_a_bucket(self, patch_drive):
        """Listing a file's children returns [], which would otherwise
        inflate the scanned-bucket count with a folder that never was."""
        drive = patch_drive(
            _drive(children=[_folder("d1"), _file("stray")], old_files=[])
        )
        summary = voicenotes_cleanup.fn()
        assert summary["date_folders_scanned"] == 1
        # Only the real bucket was walked (root listing + that bucket).
        assert drive.list_files.call_count == 2

    def test_deletes_every_file_past_retention(self, patch_drive):
        drive = patch_drive(
            _drive(children=[_folder("d1")], old_files=[_file("a"), _file("b")])
        )
        summary = voicenotes_cleanup.fn()
        assert summary["deleted"] == 2
        assert summary["failed"] == 0
        assert summary["attempted"] == 2
        assert drive.delete_file.call_count == 2


class TestReporting:
    """The sweep must not describe a failure as a success."""

    def test_total_failure_is_reported_as_an_error(self, patch_drive, caplog):
        patch_drive(
            _drive(
                children=[_folder("d1")],
                old_files=[_file("a"), _file("b")],
                delete_side_effect=PermissionError("insufficientFilePermissions"),
            )
        )
        with caplog.at_level("ERROR"):
            summary = voicenotes_cleanup.fn()

        assert summary["deleted"] == 0
        assert summary["failed"] == 2
        assert summary["attempted"] == 2
        assert "cleanup.failure" in caplog.text
        assert "cleanup.success" not in caplog.text

    def test_partial_failure_is_reported_as_degraded_not_success(
        self, patch_drive, caplog
    ):
        patch_drive(
            _drive(
                children=[_folder("d1")],
                old_files=[_file("a"), _file("b")],
                delete_side_effect=[None, RuntimeError("boom")],
            )
        )
        with caplog.at_level("WARNING"):
            summary = voicenotes_cleanup.fn()

        assert (summary["deleted"], summary["failed"]) == (1, 1)
        assert "cleanup.degraded" in caplog.text
        assert "cleanup.success" not in caplog.text

    def test_clean_sweep_is_reported_as_success(self, patch_drive, caplog):
        patch_drive(_drive(children=[_folder("d1")], old_files=[_file("a")]))
        with caplog.at_level("INFO"):
            summary = voicenotes_cleanup.fn()

        assert summary["failed"] == 0
        assert "cleanup.success" in caplog.text

    def test_first_error_is_carried_on_the_summary(self, patch_drive):
        """The caller turns this into a finding; without it the operator
        has to go find the reason in the logs."""
        patch_drive(
            _drive(
                children=[_folder("d1")],
                old_files=[_file("a")],
                delete_side_effect=PermissionError("insufficientFilePermissions"),
            )
        )
        summary = voicenotes_cleanup.fn()
        assert "insufficientFilePermissions" in summary["first_error"]

    def test_no_error_recorded_when_everything_worked(self, patch_drive):
        patch_drive(_drive(children=[_folder("d1")], old_files=[_file("a")]))
        assert voicenotes_cleanup.fn()["first_error"] is None


class TestRetentionClock:
    """Retention runs on the bucket's archive date, not the audio's mtime."""

    def test_recent_bucket_is_left_alone_whatever_the_files_look_like(
        self, patch_drive
    ):
        """A note recorded long ago but archived yesterday must survive.

        This is the case the old modifiedTime filter got wrong: the
        audio's timestamp says three weeks, the bucket says one day, and
        the setting promises days in processed/.
        """
        drive = patch_drive(
            _drive(children=[_folder("d1", _bucket_name(1))], old_files=[_file("a")])
        )
        summary = voicenotes_cleanup.fn()
        assert summary["attempted"] == 0
        drive.delete_file.assert_not_called()

    def test_expired_bucket_is_drained_whole(self, patch_drive):
        """Every file in a per-day bucket was archived that day, so no
        per-file timestamp is consulted."""
        drive = patch_drive(
            _drive(
                children=[_folder("d1", _bucket_name(30))],
                old_files=[_file("a"), _file("b")],
            )
        )
        summary = voicenotes_cleanup.fn()
        assert summary["deleted"] == 2
        drive.list_files_older_than.assert_not_called()

    def test_bucket_exactly_at_the_window_edge_is_kept(self, patch_drive):
        drive = patch_drive(
            _drive(children=[_folder("d1", _bucket_name(14))], old_files=[_file("a")])
        )
        assert voicenotes_cleanup.fn()["attempted"] == 0
        drive.delete_file.assert_not_called()

    def test_legacy_monthly_bucket_falls_back_to_per_file_timestamps(self, patch_drive):
        """``2026-05/`` carries no single archive date, so the old filter
        still applies — those buckets only shrink."""
        drive = patch_drive(
            _drive(children=[_folder("d1", "2026-05")], old_files=[_file("a")])
        )
        summary = voicenotes_cleanup.fn()
        assert summary["deleted"] == 1
        drive.list_files_older_than.assert_called_once()

    def test_hand_made_folder_falls_back_rather_than_being_skipped(self, patch_drive):
        """An operator-created folder must not become a retention hole."""
        drive = patch_drive(
            _drive(children=[_folder("d1", "misc")], old_files=[_file("a")])
        )
        assert voicenotes_cleanup.fn()["deleted"] == 1
        drive.list_files_older_than.assert_called_once()


class TestDeletionIsReportable:
    """A permanent delete must be able to reach the run's notification."""

    def test_summary_carries_the_cutoff_so_the_notice_can_name_a_date(
        self, patch_drive
    ):
        patch_drive(
            _drive(children=[_folder("d1", _bucket_name(30))], old_files=[_file("a")])
        )
        summary = voicenotes_cleanup.fn()
        expected = (datetime.now(UTC).date() - timedelta(days=14)).isoformat()
        assert summary["cutoff_date"] == expected
