"""Unit tests for the voicenotes batch-outcome report.

Two responsibilities:

1. ``build_report`` — folding a batch outcome (files seen and processed,
   per-file failures, what the run created or removed) into the one
   :class:`RunReport` this cog sends.
2. ``emit_evaluation`` — sending it once, with the right source and
   notability, and never raising.

The library itself is exhaustively tested in common-python-utils; these
tests stop at this cog's adapter. What they are here to hold down is the
regression that motivated it: a processed voice note whose message said
how many files it had seen and nothing about the Asana task it created.
"""

from __future__ import annotations

from unittest.mock import MagicMock, patch

from mini_app_polis.pipeline_status import CREATED, REMOVED

from transcription_cog.voicenotes.tasks.emit_evaluation import (
    build_report,
    emit_evaluation,
)

_SEND = "mini_app_polis.pipeline_status.RunReport.send"


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


def _task(title: str = "Send the floor trials report", gid: str = "as-100") -> dict:
    return {
        "op": CREATED,
        "kind": "asana task",
        "item": title,
        "link": f"https://app.asana.com/0/proj/{gid}",
    }


class TestSeverity:
    """Batch outcome → one severity."""

    def test_clean_batch_is_success(self) -> None:
        assert build_report(files_seen=3, files_processed=3).severity == "SUCCESS"

    def test_batch_with_failures_is_warn(self) -> None:
        report = build_report(
            files_seen=3, files_processed=1, findings=[_failure("boom")]
        )
        assert report.severity == "WARN"

    def test_a_failed_batch_is_warn_not_error(self) -> None:
        """ERROR means the flow died, and this task cannot know that.

        The old adapter reported every batch with a failed file as ERROR.
        A run that completed and flagged some files is a WARN — results
        worth a human look — and the on_failure hook owns the case where
        the flow itself went down, with the Prefect state that caused it.
        """
        report = build_report(
            files_seen=2,
            files_processed=0,
            findings=[_failure("worker died", failed_at="transcribe")],
        )
        assert report.severity == "WARN"

    def test_outcomes_never_raise_severity(self) -> None:
        report = build_report(files_seen=1, files_processed=1, outcomes=[_task()])
        assert report.severity == "SUCCESS"


class TestWhatTheRunMade:
    """The regression this adapter exists for."""

    def test_the_asana_task_is_in_the_message(self) -> None:
        report = build_report(files_seen=1, files_processed=1, outcomes=[_task()])
        text = report.text()
        assert "+ asana task: [Send the floor trials report]" in text
        assert "https://app.asana.com/0/proj/as-100" in text

    def test_a_run_that_created_nothing_says_nothing_about_tasks(self) -> None:
        """A replayed file whose task already existed is the guard working."""
        report = build_report(files_seen=1, files_processed=1, outcomes=[])
        assert "asana task" not in report.text()

    def test_trashed_recordings_are_a_removal_not_a_footnote(self) -> None:
        """The retention sweep's old bespoke 'notice' is an ordinary outcome."""
        report = build_report(
            files_seen=0,
            files_processed=0,
            outcomes=[
                {
                    "op": REMOVED,
                    "kind": "recording",
                    "item": "4 archived before 2026-08-12",
                }
            ],
        )
        assert "- recording: 4 archived before 2026-08-12" in report.text()

    def test_several_tasks_group_onto_one_line(self) -> None:
        report = build_report(
            files_seen=3,
            files_processed=3,
            outcomes=[_task(f"note {i}", f"as-{i}") for i in range(3)],
        )
        lines = [ln for ln in report.text().splitlines() if ln.startswith("+")]
        assert len(lines) == 1
        assert lines[0].count("asana task") == 1

    def test_an_unknown_op_still_reaches_the_message(self) -> None:
        """The library walks a fixed list of three; a typo must not vanish."""
        report = build_report(
            files_seen=1,
            files_processed=1,
            outcomes=[{"op": "made", "kind": "asana task", "item": "x"}],
        )
        assert "asana task: x" in report.text()


class TestFailures:
    """Per-file failures become issues, grouped by the step that broke."""

    def test_failures_are_grouped_by_step_and_name_the_file(self) -> None:
        report = build_report(
            files_seen=2,
            files_processed=0,
            findings=[
                {**_failure("500 from whisper", "id1", "transcribe"), "name": "a.m4a"},
                {**_failure("500 from whisper", "id2", "transcribe"), "name": "b.m4a"},
            ],
        )
        line = [ln for ln in report.text().splitlines() if ln.startswith("transcribe:")]
        assert len(line) == 1
        assert "a.m4a" in line[0] and "b.m4a" in line[0]

    def test_the_drive_id_is_used_when_there_is_no_name(self) -> None:
        report = build_report(
            files_seen=1,
            files_processed=0,
            findings=[_failure("boom", "id9", "extract")],
        )
        assert "id9" in report.text()

    def test_a_finding_with_no_step_still_lands_somewhere_named(self) -> None:
        report = build_report(
            files_seen=1, files_processed=0, findings=[_failure("boom")]
        )
        assert "failed:" in report.text()

    def test_the_failure_detail_is_carried(self) -> None:
        report = build_report(
            files_seen=1,
            files_processed=0,
            findings=[_failure("429 rate limited", "id1", "transcribe")],
        )
        assert "429 rate limited" in report.text()


class TestCounters:
    """Seen is not processed, and the gap is the interesting part."""

    def test_files_seen_is_carried_as_a_counter(self) -> None:
        report = build_report(files_seen=4, files_processed=1)
        assert report.counters["seen"] == 4

    def test_the_headline_counts_work_done_not_files_found(self) -> None:
        report = build_report(files_seen=4, files_processed=1, duration_sec=1.0)
        assert "processed=1" in report.text().splitlines()[0]

    def test_a_non_file_problem_does_not_inflate_the_file_count(self) -> None:
        """A failed retention sweep is one issue and zero failed files."""
        report = build_report(
            files_seen=1,
            files_processed=1,
            findings=[_failure("sweep raised", "cleanup", "cleanup")],
            duration_sec=1.0,
        )
        assert "processed=1" in report.text().splitlines()[0]


class TestDuration:
    """How long it took, on every report."""

    def test_the_duration_is_on_the_headline(self) -> None:
        report = build_report(files_seen=1, files_processed=1, duration_sec=12.43)
        assert report.text().splitlines()[0].startswith("Run complete in 12.4s")

    def test_an_unsupplied_duration_is_measured_rather_than_missing(self) -> None:
        report = build_report(files_seen=0, files_processed=0)
        assert "Run complete in " in report.text()


class TestEmitEvaluation:
    """The task sends the report once, and never raises."""

    def test_sends_once(self) -> None:
        with patch(_SEND) as send:
            send.return_value = MagicMock(sent=1, suppressed=0, failed=0)
            emit_evaluation.fn(flow_run_id="run-1", files_seen=2, files_processed=2)
        send.assert_called_once()

    def test_every_run_is_notable(self) -> None:
        """This deployment has no cron, so there are no idle runs to skip."""
        with patch(_SEND) as send:
            send.return_value = MagicMock(sent=1, suppressed=0, failed=0)
            emit_evaluation.fn(flow_run_id="run-1", files_seen=4, files_processed=4)
        assert send.call_args.kwargs["notable"] is True

    def test_empty_scan_still_reports(self) -> None:
        """The mismatch case, and the reason this is unconditional.

        The flow only runs because watcher-cog fired it. An empty scan
        therefore means the watcher saw files and this run found none —
        a race, a filter, or a bug. Gating on files_seen would silence
        exactly that run and leave the watcher's "2 new" unanswered.
        """
        with patch(_SEND) as send:
            send.return_value = MagicMock(sent=1, suppressed=0, failed=0)
            emit_evaluation.fn(flow_run_id="run-1", files_seen=0, files_processed=0)
        assert send.call_args.kwargs["notable"] is True

    def test_source_override_is_forwarded(self) -> None:
        with patch(_SEND) as send:
            send.return_value = MagicMock(sent=1, suppressed=0, failed=0)
            emit_evaluation.fn(
                flow_run_id="run-1",
                files_seen=1,
                files_processed=0,
                findings=[_failure("terminal: worker died")],
                source="flow_hook",
            )
        assert send.call_args.kwargs["source"] == "flow_hook"

    def test_one_message_for_a_batch_of_failures(self) -> None:
        """Three failed files, one notification."""
        with patch(_SEND) as send:
            send.return_value = MagicMock(sent=1, suppressed=0, failed=0)
            emit_evaluation.fn(
                flow_run_id="run-1",
                files_seen=3,
                files_processed=0,
                findings=[
                    _failure(f"bad {i}", f"id{i}", "transcribe") for i in range(3)
                ],
            )
        send.assert_called_once()

    def test_swallows_library_exception(self) -> None:
        """The library is best-effort; a raise here must not fail the flow.

        Reporting on a batch must never be the reason the batch is
        recorded as failed. The task returning normally *is* the
        assertion that nothing propagated — a raise would surface as this
        test erroring, so there is no need to catch it by hand. The call
        is verified as well, so a version of this that silently stopped
        reporting would fail rather than pass.
        """
        with patch(_SEND, side_effect=RuntimeError("boom")) as send:
            result = emit_evaluation.fn(
                flow_run_id="run-1", files_seen=1, files_processed=1
            )

        assert result is None
        send.assert_called_once()
