"""The Lambda handler, end to end, on realistic SQS events (TEST-015).

test_flow.py drives ``process_transcript`` directly, and the worker's unit
tests replace the flows with mocks. Neither proves what the queue depends
on: that a record going through ``lambda_handler`` reaches the real flow,
and that what the handler returns tells the event source mapping exactly
which records to keep. These tests run the real handler and the real
flow, and stub only what lies outside the process — Drive, the Kaiano API,
the LLM, the run report, and the SSM settings refresh.

Two cases the queue makes essential:

  Redelivery — SQS delivers at least once. The same message handled twice
    has one effect: one transcript, one source, one archive move.
  A bad record in a batch — it comes back in ``batchItemFailures``, alone,
    while the rest of the batch completes and is deleted.

Drive and the API are fakes with state, not mocks, because redelivery is a
question about state: whether the second delivery sees what the first one
did. The fake API upserts transcripts on their drive_file_id and sources on
their transcript, which is what api-kaianolevine-com does and what the
worker's retry-safety argument (``worker.process_message``) rests on.
"""

from __future__ import annotations

import json
import uuid
from collections.abc import Iterator
from dataclasses import dataclass, field
from types import SimpleNamespace
from typing import Any
from unittest.mock import MagicMock, patch

import pytest

from transcription_cog import worker

INPUT = "input-folder"
PROCESSED = "processed-folder"
QUEUE_ARN = "arn:aws:sqs:us-east-1:123456789012:transcription-jobs"

_ENV_VARS = {
    "NOTES_INPUT_FOLDER_ID": INPUT,
    "NOTES_PROCESSED_FOLDER_ID": PROCESSED,
    "KAIANO_API_BASE_URL": "http://localhost:8000",
    "LLM_PROVIDER": "anthropic",
    "ANTHROPIC_API_KEY": "test-anthropic-key",
}

_NOTES = {"title": "Test lesson", "summary": "A test lesson about leading."}
_TEXT = "A" * 300


# ── the boundary: fakes with state ──────────────────────────────────────────


@dataclass
class _File:
    id: str
    name: str
    mime_type: str = "text/plain"


class _FilesResource:
    """The slice of ``drive.service.files()`` that archive_file reads."""

    def __init__(self, drive: _FakeDrive) -> None:
        self._drive = drive
        self._file_id = ""

    def get(self, *, fileId: str, fields: str) -> _FilesResource:  # noqa: N803 — Drive's name
        self._file_id = fileId
        return self

    def execute(self) -> dict[str, list[str]]:
        return {"parents": [self._drive.parent_of(self._file_id)]}


class _FakeDrive:
    """Folders of files. Moving a file takes it out of the input folder."""

    def __init__(self) -> None:
        self.folders: dict[str, dict[str, _File]] = {INPUT: {}, PROCESSED: {}}
        self.moves: list[tuple[str, str]] = []
        self.unreadable: set[str] = set()
        self.unmovable: set[str] = set()
        self.service = SimpleNamespace(files=lambda: _FilesResource(self))

    def add(self, file_id: str, name: str) -> None:
        self.folders[INPUT][file_id] = _File(file_id, name)

    def parent_of(self, file_id: str) -> str:
        return next(f for f, files in self.folders.items() if file_id in files)

    def get_files_in_folder(
        self, folder_id: str, include_folders: bool = False
    ) -> list[_File]:
        return list(self.folders.get(folder_id, {}).values())

    def download_bytes(self, file_id: str) -> bytes:
        if file_id in self.unreadable:
            raise RuntimeError(f"drive read exploded for {file_id}")
        return _TEXT.encode()

    def move_file(self, file_id: str, *, new_parent_id: str) -> None:
        if file_id in self.unmovable:
            raise RuntimeError(f"drive move exploded for {file_id}")
        moved = self.folders[self.parent_of(file_id)].pop(file_id)
        self.folders[new_parent_id][file_id] = moved
        self.moves.append((file_id, new_parent_id))


@dataclass
class _FakeApi:
    """Transcripts upserted on drive_file_id, sources on transcript_id."""

    transcripts: dict[str, str] = field(default_factory=dict)  # drive_file_id → id
    sources: dict[str, str] = field(default_factory=dict)  # transcript_id → id
    transcript_writes: int = 0
    source_writes: int = 0

    def create_wcs_transcript(self, payload: Any) -> SimpleNamespace:
        self.transcript_writes += 1
        tid = self.transcripts.setdefault(payload.drive_file_id, str(uuid.uuid4()))
        return SimpleNamespace(id=tid)

    def create_wcs_source(self, payload: Any) -> SimpleNamespace:
        self.source_writes += 1
        sid = self.sources.setdefault(str(payload.transcript_id), str(uuid.uuid4()))
        return SimpleNamespace(id=sid)


@dataclass
class _Boundary:
    drive: _FakeDrive
    api: _FakeApi
    llm: MagicMock
    reports: MagicMock
    unprocessable_reports: MagicMock


@pytest.fixture
def boundary(monkeypatch: pytest.MonkeyPatch) -> Iterator[_Boundary]:
    for k, v in _ENV_VARS.items():
        monkeypatch.setenv(k, v)
    b = _Boundary(
        drive=_FakeDrive(),
        api=_FakeApi(),
        llm=MagicMock(),
        reports=MagicMock(),
        unprocessable_reports=MagicMock(),
    )
    b.llm.generate_json.return_value = MagicMock(output_json=_NOTES)
    google = SimpleNamespace(drive=b.drive)

    # SSM, not ours to call from a test.
    monkeypatch.setattr(worker, "load_secrets", lambda **_kw: None)
    monkeypatch.setattr(worker, "post_run_finding", b.unprocessable_reports)
    with (
        patch("transcription_cog.flow.GoogleAPI") as gapi,
        patch("transcription_cog.flow.KaianoApiClient") as api_client,
        patch("transcription_cog.flow.build_llm", return_value=b.llm),
        patch("mini_app_polis.pipeline_status.post_run_finding", b.reports),
    ):
        gapi.from_env.return_value = google
        api_client.from_env.return_value = b.api
        yield b


# ── realistic events ────────────────────────────────────────────────────────


def _body(drive_file_id: str, mode: str = "wcs-transcripts") -> str:
    """The message api-kaianolevine-com's transcription_dispatch enqueues."""
    return json.dumps(
        {
            "type": "transcription.run",
            "version": 1,
            "payload": {"mode": mode, "drive_file_id": drive_file_id},
        }
    )


def _record(message_id: str, body: str, *, receive_count: int = 1) -> dict[str, Any]:
    """One SQS record as Lambda's event source mapping delivers it."""
    return {
        "messageId": message_id,
        "receiptHandle": f"AQEB{message_id}{receive_count}==",
        "body": body,
        "attributes": {
            "ApproximateReceiveCount": str(receive_count),
            "SentTimestamp": "1767225600000",
            "SenderId": "AIDAEXAMPLE",
            "ApproximateFirstReceiveTimestamp": "1767225600100",
        },
        "messageAttributes": {},
        "md5OfBody": "0" * 32,
        "eventSource": "aws:sqs",
        "eventSourceARN": QUEUE_ARN,
        "awsRegion": "us-east-1",
    }


def _event(*records: dict[str, Any]) -> dict[str, Any]:
    return {"Records": list(records)}


class _Context:
    """The part of Lambda's context the handler reads: the time left."""

    def get_remaining_time_in_millis(self) -> int:
        return 900_000


def _handle(event: dict[str, Any]) -> dict[str, Any]:
    return worker.lambda_handler(event, _Context())


# ── redelivery ──────────────────────────────────────────────────────────────


def test_the_same_message_delivered_twice_has_one_effect(boundary: _Boundary) -> None:
    """SQS is at-least-once. The second delivery finds the file archived."""
    boundary.drive.add("file-1", "2026-04-01 Kaiano > Sarah - Connection.txt")
    body = _body("file-1")

    first = _handle(_event(_record("m-1", body, receive_count=1)))
    second = _handle(_event(_record("m-1", body, receive_count=2)))

    assert first == {"batchItemFailures": []}
    assert second == {"batchItemFailures": []}
    assert boundary.api.transcript_writes == 1
    assert boundary.api.source_writes == 1
    assert boundary.llm.generate_json.call_count == 1
    assert boundary.drive.moves == [("file-1", PROCESSED)]


def test_a_redelivery_after_a_partial_failure_still_has_one_effect(
    boundary: _Boundary,
) -> None:
    """Stored but not archived: the retry stores again, and the API's upsert
    leaves one transcript and one source, not two."""
    boundary.drive.add("file-1", "2026-04-01 Kaiano > Sarah - Connection.txt")
    boundary.drive.unmovable.add("file-1")
    body = _body("file-1")

    first = _handle(_event(_record("m-1", body, receive_count=1)))
    assert first == {"batchItemFailures": [{"itemIdentifier": "m-1"}]}
    assert boundary.drive.moves == []

    boundary.drive.unmovable.clear()
    second = _handle(_event(_record("m-1", body, receive_count=2)))

    assert second == {"batchItemFailures": []}
    assert len(boundary.api.transcripts) == 1
    assert len(boundary.api.sources) == 1
    assert boundary.drive.moves == [("file-1", PROCESSED)]


# ── a bad record in a batch ─────────────────────────────────────────────────


def test_a_bad_record_comes_back_alone_while_the_batch_completes(
    boundary: _Boundary,
) -> None:
    """A run that fails and a message that cannot be read are both named;
    the good records around them finish and are left for the mapping to
    delete."""
    boundary.drive.add("file-1", "2026-04-01 Kaiano > Sarah - Connection.txt")
    boundary.drive.add("file-2", "2026-04-02 Kaiano > Swingesota.txt")
    boundary.drive.add("file-3", "2026-04-03 Kaiano > Sarah - Frame.txt")
    boundary.drive.unreadable.add("file-2")

    result = _handle(
        _event(
            _record("m-1", _body("file-1")),
            _record("m-2", _body("file-2")),
            _record("m-3", "not json at all"),
            _record("m-4", _body("file-3")),
        )
    )

    assert result == {
        "batchItemFailures": [{"itemIdentifier": "m-2"}, {"itemIdentifier": "m-3"}]
    }
    # The good records completed: stored and archived.
    assert set(boundary.api.transcripts) == {"file-1", "file-3"}
    assert boundary.drive.moves == [("file-1", PROCESSED), ("file-3", PROCESSED)]
    # The failed run is still in the inbox for its retry.
    assert "file-2" in boundary.drive.folders[INPUT]
    # The failed run reported itself; the unreadable message, which reached
    # no flow, is reported by the worker — once.
    by_run = {c.kwargs["run_id"]: c.args[1] for c in boundary.reports.call_args_list}
    assert by_run["m-1"] == "SUCCESS"
    assert by_run["m-2"] != "SUCCESS"
    assert by_run["m-4"] == "SUCCESS"
    boundary.unprocessable_reports.assert_called_once()
    assert boundary.unprocessable_reports.call_args.kwargs["run_id"] == "m-3"
