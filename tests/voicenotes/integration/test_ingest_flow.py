"""End-to-end integration test for the voicenotes flows.

All external clients (Drive, Whisper, Claude, Asana,
api-kaianolevine-com) are mocked. Exercises one note end to end — find
it in the inbox → download → transcribe → extract → post → archive —
plus the retention sweep and the one report each run sends.

Covers:
  - TEST-001 normalization: a plain transcript flows through and lands
    as an Asana task with the normalized title.
  - TEST-002 deduplication: a file whose external id already exists in
    Asana skips create_task but still archives; a file an earlier job
    already archived is a quiet no-op.
  - TEST-003 failure: when Whisper fails, the file is NOT archived
    (it stays in the inbox), the run reports the failure, and the flow
    raises so the queue redelivers the job.
  - TEST-004 output shape: the flow returns the documented summary
    dict.
"""

from __future__ import annotations

import json
from types import SimpleNamespace
from unittest.mock import MagicMock

import pytest
from mini_app_polis.asana import AsanaClient

from transcription_cog.voicenotes.clients import (
    claude_client as claude_mod,
)
from transcription_cog.voicenotes.clients import (
    drive_client as drive_mod,
)
from transcription_cog.voicenotes.clients.whisper_client import TranscriptionResult
from transcription_cog.voicenotes.flows import cleanup as cleanup_mod
from transcription_cog.voicenotes.flows import ingest as ingest_mod
from transcription_cog.voicenotes.flows.ingest import (
    voicenotes_cleanup_run,
    voicenotes_ingest,
)
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

    # ---- Asana ----
    asana = MagicMock(spec=AsanaClient)
    asana.find_task_by_external_id.return_value = None
    asana.create_task.return_value = "as-100"
    asana.find_or_create_tag.side_effect = lambda name: f"tag-{name}"
    monkeypatch.setattr(post_task_mod, "get_asana_client", lambda: asana)

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
    # The library has two sinks and this fixture stands in for both, so a
    # test can say which one a code path used. Run status goes through
    # ``_deliver`` to POST /v1/notify; graded findings go through
    # ``_post_evaluation`` to POST /v1/evaluations. Patching the library
    # helpers directly captures either without standing up a real
    # KaianoApiClient, and the MagicMock's ``.post`` / ``.notify``
    # attributes mirror the client surface the assertions read.
    import mini_app_polis.pipeline_status as _pipeline_status_mod

    kaiano_client = MagicMock()
    kaiano_client.post.return_value = {"ok": True}
    kaiano_client.notify.return_value = {"data": {"forwarded": True}}

    def _fake_post_evaluation(payload):
        kaiano_client.post("/v1/evaluations", payload)
        return True

    def _fake_deliver(message, *, repo, logger):  # noqa: ANN001, ARG001
        kaiano_client.notify(embeds=message["embeds"], username=message.get("username"))
        return True

    monkeypatch.setattr(_pipeline_status_mod, "_post_evaluation", _fake_post_evaluation)
    monkeypatch.setattr(_pipeline_status_mod, "_deliver", _fake_deliver)
    # The library checks for KAIANO_API_BASE_URL before posting; tests
    # set this to a non-empty string so the gating doesn't short-circuit.
    monkeypatch.setenv("KAIANO_API_BASE_URL", "https://api.test.local")

    return SimpleNamespace(
        whisper=whisper,
        asana=asana,
        drive=drive,
        kaiano=kaiano_client,
        anthropic_create=fake_anthropic.messages.create,
    )


class TestIngestHappyPath:
    """TEST-001 + TEST-004: end-to-end normalization + output shape."""

    def test_returns_summary_dict_and_processes_the_file(self, stub_clients):
        result = voicenotes_ingest("drive-abc", run_id="msg-1")

        # TEST-004: documented summary shape.
        assert set(result.keys()) >= {
            "files_processed",
            "files_failed",
            "files_seen",
            "duration_sec",
            "total_cost_usd_estimate",
            "run_id",
            "results",
            "failures",
        }
        assert result["run_id"] == "msg-1"
        assert result["files_seen"] == 1
        assert result["files_processed"] == 1
        assert result["files_failed"] == 0
        assert result["failures"] == []
        assert isinstance(result["duration_sec"], float)

        (file_result,) = result["results"]
        assert file_result["drive_file_id"] == "drive-abc"
        assert file_result["asana_task_id"] == "as-100"
        assert file_result["needs_review"] is False

        # Drive's ``list_files`` is called twice: once to find the note in
        # the inbox, once by the retention sweep walking ``processed/``.
        from transcription_cog.voicenotes.config import settings as _settings

        assert stub_clients.drive.list_files.call_count == 2
        stub_clients.drive.list_files.assert_any_call(
            _settings.google_drive_voice_inbox_folder_id
        )
        stub_clients.drive.download_file.assert_called_once_with("drive-abc")
        stub_clients.whisper.transcribe.assert_called_once()
        stub_clients.anthropic_create.assert_called()
        stub_clients.asana.find_task_by_external_id.assert_called_once()
        stub_clients.asana.create_task.assert_called_once()
        stub_clients.drive.move_file.assert_called_once()

        # One report, naming the task it created.
        stub_clients.kaiano.notify.assert_called_once()
        stub_clients.kaiano.post.assert_not_called()

    def test_only_the_named_file_is_processed(self, stub_clients):
        """Other files in the inbox are other jobs."""
        stub_clients.drive.list_files.return_value = [
            _drive_file("drive-other"),
            _drive_file("drive-abc"),
        ]

        voicenotes_ingest("drive-abc", run_id="msg-1")

        stub_clients.drive.download_file.assert_called_once_with("drive-abc")

    def test_a_folder_is_never_a_voice_note(self, stub_clients):
        result = voicenotes_ingest("processed-folder", run_id="msg-1")

        assert result["files_seen"] == 0
        stub_clients.drive.download_file.assert_not_called()


class TestIngestDeduplication:
    """TEST-002: nothing is done twice."""

    def test_skips_create_when_marker_already_present(self, stub_clients):
        """Existing task in Asana → no duplicate create, but archive still runs."""
        stub_clients.asana.find_task_by_external_id.return_value = "as-existing"

        result = voicenotes_ingest("drive-abc", run_id="msg-1")

        assert result["files_processed"] == 1
        (file_result,) = result["results"]
        assert file_result["asana_task_id"] == "as-existing"
        stub_clients.asana.create_task.assert_not_called()
        # Archive still runs, so the file leaves the inbox.
        stub_clients.drive.move_file.assert_called_once()

    def test_a_file_already_archived_is_a_quiet_no_op(self, stub_clients):
        """watcher asks again after a partial failure or a restart.

        The note has left the inbox, so an earlier job finished it. The run
        does nothing and sends nothing: the guard working is not news.
        """
        stub_clients.drive.list_files.return_value = [
            _drive_folder("processed-folder", "processed"),
        ]

        result = voicenotes_ingest("drive-abc", run_id="msg-2")

        assert result["files_seen"] == 0
        assert result["files_failed"] == 0
        stub_clients.drive.download_file.assert_not_called()
        stub_clients.asana.create_task.assert_not_called()
        stub_clients.kaiano.notify.assert_not_called()


class TestIngestFailurePath:
    """TEST-003: a failed note is reported, left in the inbox, and raised."""

    def test_whisper_failure_is_reported_skips_archive_and_raises(self, stub_clients):
        stub_clients.whisper.transcribe.side_effect = RuntimeError("whisper boom")

        with pytest.raises(RuntimeError, match="whisper boom"):
            voicenotes_ingest("drive-abc", run_id="msg-1")

        # Not archived: the file stays in the inbox for the redelivered job.
        stub_clients.drive.move_file.assert_not_called()
        stub_clients.asana.create_task.assert_not_called()
        # Reported once, before the raise — the worker sends nothing more.
        stub_clients.kaiano.notify.assert_called_once()

    def test_the_report_names_the_step_that_failed(self, stub_clients, monkeypatch):
        emit = MagicMock()
        monkeypatch.setattr(ingest_mod, "emit_evaluation", emit)
        stub_clients.whisper.transcribe.side_effect = RuntimeError("whisper boom")

        with pytest.raises(RuntimeError):
            voicenotes_ingest("drive-abc", run_id="msg-1")

        kwargs = emit.call_args.kwargs
        assert kwargs["run_id"] == "msg-1"
        assert kwargs["files_seen"] == 1
        assert kwargs["files_processed"] == 0
        (finding,) = [f for f in kwargs["findings"] if f["failed_at_task"] != "cleanup"]
        assert finding["failed_at_task"] == "transcribe"
        assert "whisper boom" in finding["message"]
        assert kwargs["notable"] is True

    def test_the_retention_sweep_still_runs_after_a_failed_note(self, stub_clients):
        stub_clients.whisper.transcribe.side_effect = RuntimeError("whisper boom")

        with pytest.raises(RuntimeError):
            voicenotes_ingest("drive-abc", run_id="msg-1")

        # The inbox lookup, then the sweep's walk of processed/.
        assert stub_clients.drive.list_files.call_count == 2

    def test_out_of_time_skips_the_sweep_and_still_reports(self, stub_clients):
        """What is left of the margin belongs to the report, not the archive."""
        from transcription_cog._deadline import RunOutOfTime

        stub_clients.whisper.transcribe.side_effect = RunOutOfTime("stopped")

        with pytest.raises(RunOutOfTime):
            voicenotes_ingest("drive-abc", run_id="msg-1")

        # Only the inbox lookup: the sweep's walk of processed/ never ran.
        assert stub_clients.drive.list_files.call_count == 1
        stub_clients.kaiano.notify.assert_called_once()

    def test_missing_configuration_is_reported_and_raised(
        self, stub_clients, monkeypatch
    ):
        from transcription_cog.voicenotes.config import settings as _settings

        monkeypatch.setattr(_settings, "openai_api_key", "")

        with pytest.raises(RuntimeError, match="OPENAI_API_KEY"):
            voicenotes_ingest("drive-abc", run_id="msg-1")

        stub_clients.drive.download_file.assert_not_called()
        stub_clients.kaiano.notify.assert_called_once()


class TestCleanupRun:
    """The retention sweep asked for on its own reports as its own run."""

    def test_reports_under_its_own_flow_name(self, stub_clients, monkeypatch):
        emit = MagicMock()
        monkeypatch.setattr(ingest_mod, "emit_evaluation", emit)

        voicenotes_cleanup_run(run_id="msg-9")

        emit.assert_called_once()
        assert emit.call_args.kwargs["flow_name"] == "voicenotes-cleanup"
        assert emit.call_args.kwargs["run_id"] == "msg-9"
        stub_clients.drive.download_file.assert_not_called()
