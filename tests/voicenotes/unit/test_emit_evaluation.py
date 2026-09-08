"""Unit tests for the voicenotes batch-outcome report.

Two responsibilities:

1. ``_summarise`` — folding a batch outcome (success flag, per-file
   failures, files seen) into one severity and one message body.
2. ``emit_evaluation`` — calling
   :func:`transcription_cog._pipeline_eval.post_run_finding` once, with
   the right severity, text, source and notability.

The library itself is exhaustively tested in common-python-utils; these
tests stop at this cog's adapter.
"""

from __future__ import annotations

from unittest.mock import MagicMock, patch

from transcription_cog.voicenotes.tasks.emit_evaluation import (
    _MAX_LISTED_FAILURES,
    _summarise,
    emit_evaluation,
)

_POST = "transcription_cog.voicenotes.tasks.emit_evaluation.post_run_finding"


def _failure(message: str, file_id: str = "f1", failed_at: str | None = None) -> dict:
    row: dict = {
        "category": "pipeline",
        "severity": "ERROR",
        "message": message,
        "drive_file_id": file_id,
    }
    if failed_at:
        row["failed_at_task"] = failed_at
    return row


class TestSummarise:
    """Batch outcome → one severity, one body."""

    def test_clean_batch_is_success(self):
        severity, text = _summarise(
            drive_file_id="batch", success=True, findings=[], files_seen=3
        )
        assert severity == "SUCCESS"
        assert "3 file(s) seen" in text

    def test_batch_with_failures_is_warn(self):
        """Some files failed but the batch finished — a WARN, not an ERROR."""
        severity, text = _summarise(
            drive_file_id="batch",
            success=False,
            findings=[_failure("bad audio")],
            files_seen=3,
        )
        # success=False is what the flow passes when any file failed.
        assert severity == "ERROR"
        assert "1 failed" in text

    def test_partial_batch_that_still_succeeded_is_warn(self):
        severity, _ = _summarise(
            drive_file_id="batch",
            success=True,
            findings=[_failure("one hiccup")],
            files_seen=4,
        )
        assert severity == "WARN"

    def test_terminal_failure_without_findings(self):
        severity, text = _summarise(
            drive_file_id="batch", success=False, findings=None, files_seen=None
        )
        assert severity == "ERROR"
        assert "terminal failure" in text

    def test_per_file_failures_become_lines_in_one_message(self):
        _, text = _summarise(
            drive_file_id="batch",
            success=False,
            findings=[
                _failure("no speech detected", "aaa", "transcribe"),
                _failure("upload rejected", "bbb"),
            ],
            files_seen=2,
        )
        lines = text.splitlines()
        assert len(lines) == 3  # headline + two failures
        assert "no speech detected" in lines[1]
        assert "drive_file_id=aaa" in lines[1]
        assert "failed at transcribe" in lines[1]
        assert "upload rejected" in lines[2]

    def test_long_failure_list_is_capped_with_a_count(self):
        """A folder full of broken files is one message, not fifty."""
        findings = [_failure(f"broken {i}", f"id{i}") for i in range(25)]
        _, text = _summarise(
            drive_file_id="batch", success=False, findings=findings, files_seen=25
        )
        lines = text.splitlines()
        assert len(lines) == 1 + _MAX_LISTED_FAILURES + 1
        assert lines[-1] == f"• …and {25 - _MAX_LISTED_FAILURES} more"

    def test_batch_sentinel_is_elided_from_the_headline(self):
        _, text = _summarise(
            drive_file_id="batch", success=True, findings=[], files_seen=1
        )
        assert "drive_file_id=batch" not in text

    def test_real_drive_file_id_appears_in_the_headline(self):
        _, text = _summarise(
            drive_file_id="abc123", success=True, findings=[], files_seen=1
        )
        assert "drive_file_id=abc123" in text


class TestEmitEvaluation:
    """The task calls the shim once, with what the summary decided."""

    def test_calls_post_run_finding_once(self) -> None:
        with patch(_POST) as post:
            post.return_value = MagicMock(sent=1, suppressed=0, failed=0)
            emit_evaluation.fn(
                flow_run_id="run-1",
                drive_file_id="batch",
                success=True,
                findings=[],
                files_seen=2,
            )
        post.assert_called_once()
        assert post.call_args.args[0] == "voicenotes-ingest"
        assert post.call_args.args[1] == "SUCCESS"

    def test_failures_are_reported_as_one_call(self) -> None:
        """Three failed files, one notification."""
        with patch(_POST) as post:
            post.return_value = MagicMock(sent=1, suppressed=0, failed=0)
            emit_evaluation.fn(
                flow_run_id="run-1",
                drive_file_id="batch",
                success=False,
                findings=[_failure(f"bad {i}", f"id{i}") for i in range(3)],
                files_seen=3,
            )
        post.assert_called_once()
        text = post.call_args.args[2]
        assert text.count("•") == 3

    def test_seen_files_make_the_run_notable(self) -> None:
        """A cycle that saw files reports even when nothing changed."""
        with patch(_POST) as post:
            post.return_value = MagicMock(sent=1, suppressed=0, failed=0)
            emit_evaluation.fn(
                flow_run_id="run-1",
                drive_file_id="batch",
                success=True,
                findings=[],
                files_seen=4,
            )
        assert post.call_args.kwargs["notable"] is True

    def test_empty_cycle_is_not_notable(self) -> None:
        """An idle poll over an empty folder says nothing."""
        with patch(_POST) as post:
            post.return_value = MagicMock(sent=0, suppressed=1, failed=0)
            emit_evaluation.fn(
                flow_run_id="run-1",
                drive_file_id="batch",
                success=True,
                findings=[],
                files_seen=0,
            )
        assert post.call_args.kwargs["notable"] is False

    def test_source_override_is_forwarded(self) -> None:
        with patch(_POST) as post:
            post.return_value = MagicMock(sent=1, suppressed=0, failed=0)
            emit_evaluation.fn(
                flow_run_id="run-1",
                drive_file_id="batch",
                success=False,
                findings=[_failure("terminal: worker died")],
                source="flow_hook",
            )
        assert post.call_args.kwargs["source"] == "flow_hook"

    def test_swallows_library_exception(self) -> None:
        """The library is best-effort; a raise here must not fail the flow.

        Reporting on a batch must never be the reason the batch is
        recorded as failed. The task returning normally *is* the
        assertion that nothing propagated — a raise would surface as this
        test erroring, so there is no need to catch it by hand. The call
        is verified as well, so a version of this that silently stopped
        reporting would fail rather than pass.
        """
        with patch(_POST, side_effect=RuntimeError("boom")) as post:
            result = emit_evaluation.fn(
                flow_run_id="run-1",
                drive_file_id="batch",
                success=True,
                findings=[],
                files_seen=1,
            )

        assert result is None
        post.assert_called_once()
