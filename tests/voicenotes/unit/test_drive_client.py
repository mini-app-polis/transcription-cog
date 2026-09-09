"""Unit tests for ``DriveClient`` retention scanning.

``list_files_older_than`` is the only input the cleanup flow has. A
file it declines to return is exempt from retention forever, and
cleanup reports ``deleted=0, failed=0`` either way — so the reason a
file was passed over has to leave a trace somewhere.
"""

from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import MagicMock

from transcription_cog.voicenotes.clients import drive_client as drive_mod


def _drive_file(file_id: str, name: str, modified_time: str | None):
    return SimpleNamespace(id=file_id, name=name, modified_time=modified_time)


def test_undatable_files_are_logged_by_the_retention_sweep(monkeypatch) -> None:
    fake_logger = MagicMock()
    monkeypatch.setattr(drive_mod, "_logger", fake_logger)

    google_api = MagicMock()
    google_api.drive.list_files.return_value = [
        _drive_file("a", "no-timestamp.m4a", None),
        _drive_file("b", "bad-timestamp.m4a", "not-a-date"),
        _drive_file("c", "genuinely-old.m4a", "2000-01-01T00:00:00Z"),
    ]
    client = drive_mod.DriveClient(google_api=google_api)

    old = client.list_files_older_than("date-folder", days=30)

    assert [f.id for f in old] == ["c"]
    fake_logger.warning.assert_called_once()
    context = fake_logger.warning.call_args.kwargs["context"]
    assert context["undatable"] == 2
    assert context["files_seen"] == 3
    assert context["folder_id"] == "date-folder"


def test_a_fully_datable_folder_stays_quiet(monkeypatch) -> None:
    """The warning has to mean something, so it can't fire on every sweep."""
    fake_logger = MagicMock()
    monkeypatch.setattr(drive_mod, "_logger", fake_logger)

    google_api = MagicMock()
    google_api.drive.list_files.return_value = [
        _drive_file("a", "recent.m4a", "2999-01-01T00:00:00Z"),
        _drive_file("b", "genuinely-old.m4a", "2000-01-01T00:00:00Z"),
    ]
    client = drive_mod.DriveClient(google_api=google_api)

    old = client.list_files_older_than("date-folder", days=30)

    assert [f.id for f in old] == ["b"]
    fake_logger.warning.assert_not_called()
