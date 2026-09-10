"""Tests for the retention sweep — counting, filtering, and honest reporting.

There was no test module here, which is how ``deleted=0 failed=38`` came
to be logged as ``voicenotes.cleanup.success`` in production. The
assertions below are mostly about what the sweep *says* it did.

Two fixture rules keep this module from rotting the way it did once
already. Bucket names are never hard-coded dates: a fixed name is inside
the retention window on the day it is written and outside it forever
after, so every date here is computed from ``now``. And the stub trashes
rather than deletes, because that is what the flow calls — a MagicMock
answers any method you invent, so stubbing ``delete_file`` produced a
drive that always "succeeded" and tests that could not see a failure.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from types import SimpleNamespace
from unittest.mock import MagicMock

import pytest

from transcription_cog.voicenotes.flows import cleanup as cleanup_mod
from transcription_cog.voicenotes.flows.cleanup import voicenotes_cleanup

_FOLDER_MIME = "application/vnd.google-apps.folder"


def _folder(file_id: str, name: str = "2026-08"):
    """A bucket whose name carries no single archive date.

    ``YYYY-MM`` is the legacy monthly layout, so the sweep falls back to
    per-file ``modifiedTime`` via ``list_files_older_than`` — which is
    the path every counting and reporting test below exercises. The name
    deliberately does not parse as ``YYYY-MM-DD``; use
    :func:`_dated_folder` for the daily-bucket path.
    """
    return SimpleNamespace(id=file_id, name=name, mime_type=_FOLDER_MIME)


def _dated_folder(file_id: str, *, days_ago: int):
    """A daily bucket dated relative to now, so it cannot rot.

    ``days_ago`` greater than the retention window puts the bucket past
    the cutoff and the whole folder is retired; less than it leaves the
    bucket untouched.
    """
    day = datetime.now(UTC).date() - timedelta(days=days_ago)
    return SimpleNamespace(id=file_id, name=day.isoformat(), mime_type=_FOLDER_MIME)


def _file(file_id: str, name: str = "note.m4a"):
    return SimpleNamespace(id=file_id, name=name, mime_type="audio/mp4")


def _drive(*, children, old_files, bucket_files=None, trash_side_effect=None):
    """Build a DriveClient stub for one processed/ root.

    ``list_files`` is keyed by folder id. A single ``return_value`` hands
    the root's own children back for every call, so a bucket appears to
    contain itself and the sweep trashes the folder as one of its files.
    """
    drive = MagicMock()
    drive.ensure_subfolder.return_value = "processed-root"
    drive.list_files.side_effect = lambda folder_id: (
        list(children) if folder_id == "processed-root" else list(bucket_files or [])
    )
    drive.list_files_older_than.return_value = old_files
    if trash_side_effect is not None:
        drive.trash_file.side_effect = trash_side_effect
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
        assert summary["trashed"] == 0
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
        # The stray was never walked, and the dated bucket is drained
        # via list_files (measured by bucket date), not the legacy
        # per-file list_files_older_than path.
        assert drive.list_files.call_count == 2
        assert drive.list_files_older_than.call_count == 0

    def test_deletes_every_file_past_retention(self, patch_drive):
        drive = patch_drive(
            _drive(children=[_folder("d1")], old_files=[_file("a"), _file("b")])
        )
        summary = voicenotes_cleanup.fn()
        assert summary["trashed"] == 2
        assert summary["failed"] == 0
        assert summary["attempted"] == 2
        assert drive.trash_file.call_count == 2


class TestDatedBuckets:
    """The daily-bucket path: the folder name carries the archive date,
    so the sweep retires the whole bucket without reading file times."""

    def test_bucket_past_retention_is_drained_and_retired(self, patch_drive):
        drive = patch_drive(
            _drive(
                children=[_dated_folder("d1", days_ago=30)],
                old_files=[],
                bucket_files=[_file("a"), _file("b")],
            )
        )
        summary = voicenotes_cleanup.fn()
        assert summary["trashed"] == 2
        assert summary["buckets_trashed"] == 1
        assert summary["failed"] == 0
        # The date is on the folder, so per-file timestamps are never read.
        assert drive.list_files_older_than.call_count == 0

    def test_bucket_inside_retention_is_left_alone(self, patch_drive):
        drive = patch_drive(
            _drive(
                children=[_dated_folder("d1", days_ago=1)],
                old_files=[],
                bucket_files=[_file("a")],
            )
        )
        summary = voicenotes_cleanup.fn()
        assert summary["attempted"] == 0
        assert summary["buckets_trashed"] == 0
        assert summary["date_folders_scanned"] == 1
        assert drive.trash_file.call_count == 0

    def test_a_bucket_that_would_not_drain_keeps_its_folder(self, patch_drive):
        """A folder still holding a file that would not trash must stay
        visible — retiring it would hide the file that failed."""
        patch_drive(
            _drive(
                children=[_dated_folder("d1", days_ago=30)],
                old_files=[],
                bucket_files=[_file("a")],
                trash_side_effect=PermissionError("insufficientFilePermissions"),
            )
        )
        summary = voicenotes_cleanup.fn()
        assert summary["failed"] == 1
        assert summary["buckets_trashed"] == 0


class TestReporting:
    """The sweep must not describe a failure as a success."""

    def test_total_failure_is_reported_as_an_error(self, patch_drive, caplog):
        patch_drive(
            _drive(
                children=[_folder("d1")],
                old_files=[_file("a"), _file("b")],
                trash_side_effect=PermissionError("insufficientFilePermissions"),
            )
        )
        with caplog.at_level("ERROR"):
            summary = voicenotes_cleanup.fn()

        assert summary["trashed"] == 0
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
                trash_side_effect=[None, RuntimeError("boom")],
            )
        )
        with caplog.at_level("WARNING"):
            summary = voicenotes_cleanup.fn()

        assert (summary["trashed"], summary["failed"]) == (1, 1)
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
                trash_side_effect=PermissionError("insufficientFilePermissions"),
            )
        )
        summary = voicenotes_cleanup.fn()
        assert "insufficientFilePermissions" in summary["first_error"]

    def test_no_error_recorded_when_everything_worked(self, patch_drive):
        patch_drive(_drive(children=[_folder("d1")], old_files=[_file("a")]))
        assert voicenotes_cleanup.fn()["first_error"] is None
