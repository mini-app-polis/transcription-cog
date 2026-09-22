"""The Lambda entrypoint: message shape, dispatch, and the delete rule."""

from __future__ import annotations

import json
from unittest.mock import MagicMock

import pytest

from transcription_cog import _deadline, worker


def _body(
    mode: object = "wcs-transcripts", drive_file_id: object = "f-1", **overrides: object
) -> str:
    payload: dict[str, object] = {"mode": mode}
    if drive_file_id is not None:
        payload["drive_file_id"] = drive_file_id
    message: dict[str, object] = {
        "type": "transcription.run",
        "version": 1,
        "payload": payload,
    }
    message.update(overrides)
    return json.dumps(message)


def _event(*bodies: str) -> dict:
    return {
        "Records": [
            {
                "messageId": f"m-{i}",
                "body": body,
                "attributes": {"ApproximateReceiveCount": "1"},
            }
            for i, body in enumerate(bodies)
        ]
    }


@pytest.fixture
def flows(monkeypatch: pytest.MonkeyPatch) -> dict[str, MagicMock]:
    """Replace every flow; the tests are about routing, not the flows."""
    file_fakes = {mode: MagicMock(name=mode) for mode in worker.FILE_MODES}
    sweep_fakes = {mode: MagicMock(name=mode) for mode in worker.SWEEP_MODES}
    monkeypatch.setattr(worker, "FILE_MODES", file_fakes)
    monkeypatch.setattr(worker, "SWEEP_MODES", sweep_fakes)
    return {**file_fakes, **sweep_fakes}


@pytest.fixture
def reported(monkeypatch: pytest.MonkeyPatch) -> MagicMock:
    fake = MagicMock()
    monkeypatch.setattr(worker, "post_run_finding", fake)
    return fake


def test_the_router_covers_exactly_the_modes_the_api_accepts() -> None:
    """The API's TranscriptionRunRequest accepts these three and no others."""
    assert set(worker.FILE_MODES) == {"wcs-transcripts", "voicenotes"}
    assert set(worker.SWEEP_MODES) == {"voicenotes-cleanup"}


@pytest.mark.parametrize("mode", ["wcs-transcripts", "voicenotes"])
def test_a_file_message_runs_its_mode_on_its_file_under_the_message_id(
    flows: dict[str, MagicMock], reported: MagicMock, mode: str
) -> None:
    result = worker.lambda_handler(_event(_body(mode, "f-9")), None)

    assert result == {"batchItemFailures": []}
    flows[mode].assert_called_once_with("f-9", run_id="m-0")
    reported.assert_not_called()


def test_the_cleanup_message_runs_the_sweep(
    flows: dict[str, MagicMock], reported: MagicMock
) -> None:
    result = worker.lambda_handler(
        _event(_body("voicenotes-cleanup", drive_file_id=None)), None
    )

    assert result == {"batchItemFailures": []}
    flows["voicenotes-cleanup"].assert_called_once_with(run_id="m-0")


@pytest.mark.parametrize(
    "body",
    [
        "not json",
        json.dumps([1, 2]),
        _body(version=2),
        _body(type="deejay.run"),
        _body(payload="nope"),
        _body("guess"),
        # The old voicenotes-router's own mode names are not this queue's.
        _body("ingest"),
        # A file mode with no file is the folder sweep this replaced.
        _body("wcs-transcripts", drive_file_id=None),
        _body("voicenotes", drive_file_id=""),
        _body("voicenotes", drive_file_id=7),
        _body("voicenotes-cleanup", drive_file_id="f-1"),
    ],
)
def test_an_unprocessable_message_is_returned_not_dropped(
    flows: dict[str, MagicMock], reported: MagicMock, body: str
) -> None:
    """It dead-letters where someone can see it, and says so once."""
    result = worker.lambda_handler(_event(body), None)

    assert result == {"batchItemFailures": [{"itemIdentifier": "m-0"}]}
    for flow in flows.values():
        flow.assert_not_called()
    reported.assert_called_once()
    assert reported.call_args.kwargs["run_id"] == "m-0"
    assert reported.call_args.args[1] == "ERROR"


def test_a_failed_run_is_returned_and_not_reported_twice(
    flows: dict[str, MagicMock], reported: MagicMock
) -> None:
    """The flow reported itself on its way out; a second message is noise."""
    flows["voicenotes"].side_effect = RuntimeError("whisper down")

    result = worker.lambda_handler(_event(_body("voicenotes")), None)

    assert result == {"batchItemFailures": [{"itemIdentifier": "m-0"}]}
    reported.assert_not_called()


def test_only_the_failed_record_comes_back(
    flows: dict[str, MagicMock], reported: MagicMock
) -> None:
    flows["wcs-transcripts"].side_effect = [None, RuntimeError("boom")]

    result = worker.lambda_handler(
        _event(_body("wcs-transcripts", "a"), _body("wcs-transcripts", "b")), None
    )

    assert result == {"batchItemFailures": [{"itemIdentifier": "m-1"}]}
    assert [c.args for c in flows["wcs-transcripts"].call_args_list] == [("a",), ("b",)]
    reported.assert_not_called()


def test_a_failing_report_does_not_turn_a_retry_into_a_delete(
    flows: dict[str, MagicMock], reported: MagicMock
) -> None:
    reported.side_effect = RuntimeError("notify down")

    result = worker.lambda_handler(_event("not json"), None)

    assert result == {"batchItemFailures": [{"itemIdentifier": "m-0"}]}
    reported.assert_called_once()
    for flow in flows.values():
        flow.assert_not_called()


@pytest.mark.parametrize("event", [{}, {"Records": []}, None, "junk"])
def test_an_empty_or_malformed_event_is_not_an_error(event: object) -> None:
    assert worker.lambda_handler(event, None) == {"batchItemFailures": []}  # type: ignore[arg-type]


# ── the deadline ─────────────────────────────────────────────────────────


class _Context:
    """Lambda's context, as far as the worker reads it."""

    def __init__(self, remaining_ms: int) -> None:
        self._remaining_ms = remaining_ms

    def get_remaining_time_in_millis(self) -> int:
        return self._remaining_ms


def test_a_run_that_outlives_the_deadline_fails_the_ordinary_way(
    flows: dict[str, MagicMock],
    reported: MagicMock,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Stopped before Lambda kills it, so the flow can report and the message
    comes back — rather than a hard kill that tells nobody anything."""
    import time

    monkeypatch.setattr(_deadline, "DEADLINE_MARGIN_SECONDS", 0)
    raised: list[BaseException] = []

    def _slow(*_args: object, **_kwargs: object) -> None:
        try:
            time.sleep(2)
        except BaseException as exc:
            raised.append(exc)
            raise

    flows["wcs-transcripts"].side_effect = _slow

    result = worker.lambda_handler(_event(_body()), _Context(remaining_ms=100))

    assert result == {"batchItemFailures": [{"itemIdentifier": "m-0"}]}
    assert len(raised) == 1
    assert isinstance(raised[0], _deadline.RunOutOfTime)
    flows["wcs-transcripts"].assert_called_once_with("f-1", run_id="m-0")
    # The flow reports its own failure; the worker sends nothing more.
    reported.assert_not_called()


def test_the_deadline_is_cleared_after_a_run(
    flows: dict[str, MagicMock], reported: MagicMock
) -> None:
    import signal

    result = worker.lambda_handler(_event(_body()), _Context(remaining_ms=900_000))

    assert result == {"batchItemFailures": []}
    flows["wcs-transcripts"].assert_called_once_with("f-1", run_id="m-0")
    reported.assert_not_called()
    assert signal.getitimer(signal.ITIMER_REAL) == (0.0, 0.0)


def test_no_time_left_to_start_is_a_retry_not_a_run(
    flows: dict[str, MagicMock], reported: MagicMock
) -> None:
    result = worker.lambda_handler(_event(_body()), _Context(remaining_ms=1_000))

    assert result == {"batchItemFailures": [{"itemIdentifier": "m-0"}]}
    flows["wcs-transcripts"].assert_not_called()
