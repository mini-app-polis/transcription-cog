"""End-to-end integration test for the ingest flow body.

All external clients (Drive, Whisper, Claude, Todoist,
api-kaianolevine-com) are mocked. Exercises the full ingest flow
body end-to-end — scan inbox → for each file: download → transcribe
→ extract → post → archive — plus the aggregate emit_evaluation.

Covers:
  - TEST-001 normalization: a plain transcript flows through and lands
    as a Todoist task with the normalized title.
  - TEST-002 deduplication: a file whose drive_file_id marker already
    exists in Todoist skips create_task but still archives.
  - TEST-003 per-file failure isolation: when Whisper fails on one
    file, that file is NOT archived (stays in inbox for retrigger),
    but other files in the same batch still process. The batch flow
    does not raise.
  - TEST-004 output shape: the flow returns the documented summary
    dict.
  - TEST-005 empty inbox: the flow handles an empty inbox cleanly,
    emits a heartbeat evaluation, and returns zero counts.

Why ``voicenotes_ingest.fn(...)`` rather than ``voicenotes_ingest(...)``:
the latter goes through Prefect's in-memory test harness server, a
known source of 503 flakes on POST /api/flow_runs/ once enough
harness state accumulates across tests. We're verifying our flow
body's wiring, not Prefect's runtime — using ``.fn`` calls the
underlying function directly. Real flow-runtime coverage happens at
the live deploy smoke test.
"""

from __future__ import annotations

import json
from types import SimpleNamespace
from unittest.mock import MagicMock

import pytest

from transcription_cog.voicenotes.clients import (
    claude_client as claude_mod,
)
from transcription_cog.voicenotes.clients import (
    drive_client as drive_mod,
)
from transcription_cog.voicenotes.clients import (
    todoist_client as todoist_mod,
)
from transcription_cog.voicenotes.clients.whisper_client import TranscriptionResult
from transcription_cog.voicenotes.flows import cleanup as cleanup_mod
from transcription_cog.voicenotes.flows import ingest as ingest_mod
from transcription_cog.voicenotes.flows.ingest import voicenotes_ingest
from transcription_cog.voicenotes.tasks import (
    archive as archive_mod,
)
from transcription_cog.voicenotes.tasks import (
    download as download_mod,
)
from transcription_cog.voicenotes.tasks import (
    extract as extract_mod,
)
from transcription_cog.voicenotes.tasks import (
    post_task as post_task_mod,
)
from transcription_cog.voicenotes.tasks import (
    transcribe as transcribe_mod,
)


def _drive_file(file_id: str, name: str = "note.m4a", mime: str = "audio/mp4"):
    """Build a fake DriveFile-shaped record for ``list_files`` to return."""
    return SimpleNamespace(id=file_id, name=name, mime_type=mime)


def _drive_folder(file_id: str, name: str):
    """A subfolder entry — should be skipped during inbox scan."""
    return SimpleNamespace(
        id=file_id,
        name=name,
        mime_type="application/vnd.google-apps.folder",
    )


@pytest.fixture
def claude_message():
    """Build a fake Anthropic Message with a given JSON body."""

    def _build(body: dict) -> SimpleNamespace:
        return SimpleNamespace(
            content=[SimpleNamespace(text=json.dumps(body))],
            usage=SimpleNamespace(input_tokens=80, output_tokens=20),
        )

    return _build


@pytest.fixture
def stub_clients(monkeypatch, claude_message):
    """Wire mocks for every external dependency the flow touches."""
    # ---- Whisper ----
    whisper = MagicMock()
    whisper.transcribe.return_value = TranscriptionResult(
        text="remind me to send the floor trials report to Mark",
        audio_duration_sec=12.0,
        cost_usd_estimate=0.0012,
    )
    monkeypatch.setattr(transcribe_mod, "get_whisper_client", lambda: whisper)

    # ---- Claude (via fake Anthropic SDK) ----
    fake_anthropic = SimpleNamespace(
        messages=SimpleNamespace(
            create=MagicMock(
                return_value=claude_message(
                    {
                        "title": "Send floor trials report to Mark",
                        "description": "Send the floor trials report to Mark.",
                        "where": None,
                        "who": "Mark",
                        "when": None,
                        "due_date": None,
                        "labels": ["follow-up"],
                        "needs_review": False,
                    }
                )
            )
        )
    )
    real_claude = claude_mod.ClaudeClient(anthropic_client=fake_anthropic)
    monkeypatch.setattr(extract_mod, "get_claude_client", lambda: real_claude)

    # ---- Todoist ----
    todoist = MagicMock(spec=todoist_mod.TodoistClient)
    todoist.find_task_by_drive_file_id.return_value = None
    todoist.create_task.return_value = "td-100"
    monkeypatch.setattr(post_task_mod, "get_todoist_client", lambda: todoist)

    # ---- Drive (used by ingest scan, download, archive, and the
    # opportunistic cleanup that runs at the end of every ingest) ----
    drive = MagicMock(spec=drive_mod.DriveClient)
    drive.download_file.return_value = b"fake-audio-bytes"
    drive.ensure_subfolder.side_effect = lambda *_a, **_kw: "subfolder-id"
    # Default: one audio file plus the processed/ subfolder (which
    # the scanner should skip).
    drive.list_files.return_value = [
        _drive_file("drive-abc"),
        _drive_folder("processed-folder", "processed"),
    ]
    # Cleanup walks every immediate child of ``processed/`` looking
    # for files older than the retention window. Children may be
    # per-day (``YYYY-MM-DD/``, new layout) or per-month
    # (``YYYY-MM/``, legacy) — cleanup treats them uniformly. Default
    # to "nothing to delete" — tests can override per-test if
    # exercising the deletion path.
    drive.list_files_older_than.return_value = []
    monkeypatch.setattr(ingest_mod, "get_drive_client", lambda: drive)
    monkeypatch.setattr(download_mod, "get_drive_client", lambda: drive)
    monkeypatch.setattr(archive_mod, "get_drive_client", lambda: drive)
    monkeypatch.setattr(cleanup_mod, "get_drive_client", lambda: drive)

    # ---- Pipeline-status sink (used by emit_evaluation) ----
    # emit_evaluation now delegates to
    # ``mini_app_polis.pipeline_status.post_findings``, which in turn
    # calls a private ``_post_evaluation`` helper that owns the actual
    # HTTP call. We patch the library's helper directly so the test
    # captures every evaluations POST without standing up a real
    # KaianoApiClient. A MagicMock ``.post`` attribute mimics the old
    # ``kaiano_client.post`` surface so existing assertions still work.
    import mini_app_polis.pipeline_status as _pipeline_status_mod

    kaiano_client = MagicMock()
    kaiano_client.post.return_value = {"ok": True}

    def _fake_post_evaluation(payload):
        kaiano_client.post("/v1/evaluations", payload)

    monkeypatch.setattr(_pipeline_status_mod, "_post_evaluation", _fake_post_evaluation)
    # The library checks for KAIANO_API_BASE_URL before posting; tests
    # set this to a non-empty string so the gating doesn't short-circuit.
    monkeypatch.setenv("KAIANO_API_BASE_URL", "https://api.test.local")

    return SimpleNamespace(
        whisper=whisper,
        todoist=todoist,
        drive=drive,
        kaiano=kaiano_client,
        anthropic_create=fake_anthropic.messages.create,
    )


class TestIngestHappyPath:
    """TEST-001 + TEST-004: end-to-end normalization + output shape."""

    def test_returns_summary_dict_and_processes_each_audio_file(self, stub_clients):
        """One audio file → one full pipeline run + documented summary shape."""
        result = voicenotes_ingest.fn()

        # TEST-004: documented summary shape.
        assert set(result.keys()) >= {
            "files_processed",
            "files_failed",
            "files_seen",
            "duration_sec",
            "total_cost_usd_estimate",
            "flow_run_id",
            "results",
            "failures",
        }
        # 1 audio file + 1 subfolder; only the audio file is processed.
        assert result["files_seen"] == 1
        assert result["files_processed"] == 1
        assert result["files_failed"] == 0
        assert result["failures"] == []
        assert isinstance(result["duration_sec"], float)

        # The per-file result reflects the processed file.
        (file_result,) = result["results"]
        assert file_result["drive_file_id"] == "drive-abc"
        assert file_result["todoist_task_id"] == "td-100"
        assert file_result["needs_review"] is False

        # Each external dependency was called for the one file. Drive's
        # ``list_files`` is called twice per ingest cycle: once to scan
        # the inbox at the top of ``voicenotes_ingest``, once by the
        # opportunistic cleanup at the end (walking ``processed/``).
        # Assert the inbox scan happened by checking the inbox folder
        # id appears in the call args; the cleanup scan is incidental.
        from transcription_cog.voicenotes.config import settings as _settings

        assert stub_clients.drive.list_files.call_count == 2
        stub_clients.drive.list_files.assert_any_call(
            _settings.google_drive_voice_inbox_folder_id
        )
        stub_clients.drive.download_file.assert_called_once_with("drive-abc")
        stub_clients.whisper.transcribe.assert_called_once()
        stub_clients.anthropic_create.assert_called()
        stub_clients.todoist.find_task_by_drive_file_id.assert_called_once()
        stub_clients.todoist.create_task.assert_called_once()
        stub_clients.drive.move_file.assert_called_once()

    def test_subfolders_are_skipped(self, stub_clients):
        """Only files (non-folder mime type) are processed."""
        stub_clients.drive.list_files.return_value = [
            _drive_folder("f1", "processed"),
            _drive_folder("f2", "rejected"),
        ]

        result = voicenotes_ingest.fn()

        assert result["files_seen"] == 0
        assert result["files_processed"] == 0
        stub_clients.drive.download_file.assert_not_called()


class TestIngestDeduplication:
    """TEST-002: a file whose marker is already in Todoist → no duplicate."""

    def test_skips_create_when_marker_already_present(self, stub_clients):
        """Existing marker in Todoist → no duplicate create, but archive still runs."""
        stub_clients.todoist.find_task_by_drive_file_id.return_value = "td-existing"

        result = voicenotes_ingest.fn()

        assert result["files_processed"] == 1
        (file_result,) = result["results"]
        assert file_result["todoist_task_id"] == "td-existing"
        stub_clients.todoist.create_task.assert_not_called()
        # Archive still runs — file should still move out of the inbox so
        # watcher-cog stops re-triggering on the same file.
        stub_clients.drive.move_file.assert_called_once()


class TestIngestFailurePath:
    """TEST-003: per-file failure stays per-file; batch keeps going."""

    def test_whisper_failure_isolates_to_one_file_and_skips_archive(self, stub_clients):
        """Two-file batch with one Whisper failure → only the good file is archived."""
        # Two files: whisper fails on the first, succeeds on the second.
        stub_clients.drive.list_files.return_value = [
            _drive_file("drive-bad"),
            _drive_file("drive-good"),
        ]
        successes = [
            RuntimeError("whisper boom"),
            TranscriptionResult(
                text="remind me to follow up",
                audio_duration_sec=10.0,
                cost_usd_estimate=0.001,
            ),
        ]
        stub_clients.whisper.transcribe.side_effect = successes

        # Batch must NOT raise — bad file is isolated.
        result = voicenotes_ingest.fn()

        assert result["files_seen"] == 2
        assert result["files_processed"] == 1
        assert result["files_failed"] == 1
        assert result["results"][0]["drive_file_id"] == "drive-good"
        (failure,) = result["failures"]
        assert failure["drive_file_id"] == "drive-bad"
        assert failure["failed_at_task"] == "transcribe"
        assert "whisper boom" in failure["error"]

        # The bad file was NOT archived (must stay in inbox for retrigger).
        archived_ids = {
            call.args[0] for call in stub_clients.drive.move_file.call_args_list
        }
        assert "drive-bad" not in archived_ids
        assert "drive-good" in archived_ids
        # And no Todoist task was created for the bad file.
        assert stub_clients.todoist.create_task.call_count == 1


class TestIngestEmptyInbox:
    """TEST-005: empty inbox emits a heartbeat evaluation, returns zeros."""

    def test_empty_inbox_reports_nothing(self, stub_clients):
        """Empty inbox → zero counts and no notification.

        A cycle over an empty folder is an idle tick. It used to write a
        heartbeat row so the dashboard had a record of every cycle;
        Healthchecks.io answers "did it run" by firing on absence, so the
        heartbeat bought nothing and a notification for it would be pure
        noise on a scheduled poller.
        """
        stub_clients.drive.list_files.return_value = []

        result = voicenotes_ingest.fn()

        assert result["files_seen"] == 0
        assert result["files_processed"] == 0
        assert result["files_failed"] == 0
        stub_clients.kaiano.notify.assert_not_called()
        stub_clients.kaiano.post.assert_not_called()
