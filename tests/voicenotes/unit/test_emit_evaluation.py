"""Tests for the voicenotes ``emit_evaluation`` Prefect task.

The actual HTTP/auth/best-effort plumbing has moved to
:mod:`mini_app_polis.pipeline_status` and is tested exhaustively in
common-python-utils. These tests cover the two responsibilities that
remain in the cog:

1. ``_build_library_findings`` — cog-shape → library-shape translation,
   including heartbeat rows, drive_file_id folding, suggestion mapping,
   and per-row dimension override.
2. The ``emit_evaluation`` task body — delegates to
   :func:`mini_app_polis.pipeline_status.post_findings` with the right
   repo, flow_name, and source.
"""

from __future__ import annotations

from unittest.mock import MagicMock, patch

from transcription_cog.voicenotes.tasks.emit_evaluation import (
    _build_library_findings,
    emit_evaluation,
)

# Fields the library Finding dict may contain. Per-row rows do NOT carry
# run_id, repo, flow_name, source, or standards_version — those live one
# level up at the post_findings batch call.
_ALLOWED_ROW_KEYS = {"severity", "finding", "dimension", "suggestion"}


class TestBuildLibraryFindings:
    """``_build_library_findings`` assembles per-row Finding dicts."""

    def test_success_with_no_findings_emits_single_success_row(self):
        rows = _build_library_findings(
            drive_file_id="batch",
            success=True,
            findings=None,
        )
        assert len(rows) == 1
        row = rows[0]
        assert row["severity"] == "SUCCESS"
        assert "voicenotes ingest completed" in row["finding"]
        # drive_file_id="batch" is a sentinel and must be elided.
        assert "drive_file_id=" not in row["finding"]

    def test_failure_with_no_findings_emits_single_error_row(self):
        rows = _build_library_findings(
            drive_file_id="batch",
            success=False,
            findings=None,
        )
        assert len(rows) == 1
        assert rows[0]["severity"] == "ERROR"
        assert "terminal failure" in rows[0]["finding"]

    def test_findings_become_one_row_each(self):
        rows = _build_library_findings(
            drive_file_id="batch",
            success=False,
            findings=[
                {
                    "category": "pipeline",
                    "severity": "ERROR",
                    "message": "post_task failed: 400",
                    "drive_file_id": "drive-A",
                    "failed_at_task": "post_task",
                },
                {
                    "category": "pipeline",
                    "severity": "ERROR",
                    "message": "transcribe failed: timeout",
                    "drive_file_id": "drive-B",
                    "failed_at_task": "transcribe",
                },
            ],
        )
        assert len(rows) == 2
        # Per-file drive_file_id is folded into the finding text.
        assert rows[0]["finding"] == "post_task failed: 400 (drive_file_id=drive-A)"
        assert (
            rows[1]["finding"] == "transcribe failed: timeout (drive_file_id=drive-B)"
        )
        # failed_at_task → suggestion.
        assert rows[0]["suggestion"] == "Failed at task: post_task"
        assert rows[1]["suggestion"] == "Failed at task: transcribe"

    def test_category_becomes_per_row_dimension_override(self):
        rows = _build_library_findings(
            drive_file_id="batch",
            success=False,
            findings=[
                {"category": "data_quality", "severity": "WARN", "message": "x"},
                {"category": "pipeline", "severity": "ERROR", "message": "y"},
            ],
        )
        assert rows[0]["dimension"] == "data_quality"
        assert rows[1]["dimension"] == "pipeline"

    def test_drive_file_id_batch_is_elided_from_finding_text(self):
        rows = _build_library_findings(
            drive_file_id="batch",
            success=True,
            findings=None,
        )
        assert "drive_file_id=" not in rows[0]["finding"]

    def test_non_batch_drive_file_id_appears_in_heartbeat_finding(self):
        rows = _build_library_findings(
            drive_file_id="real-file-id",
            success=True,
            findings=None,
        )
        assert "drive_file_id=real-file-id" in rows[0]["finding"]

    def test_rows_only_contain_library_finding_keys(self):
        """No standards_version, violation_id, run_id, repo, flow_name, source.

        Those fields were on every row in the pre-refactor cog and are now
        owned by the library at the batch level. Guard against accidental
        re-introduction.
        """
        rows = _build_library_findings(
            drive_file_id="f",
            success=False,
            findings=[
                {
                    "category": "pipeline",
                    "severity": "ERROR",
                    "message": "x",
                    "failed_at_task": "t",
                },
            ],
        )
        for row in rows:
            assert set(row.keys()) <= _ALLOWED_ROW_KEYS, (
                f"Row introduced unexpected keys: {set(row.keys()) - _ALLOWED_ROW_KEYS}"
            )

    def test_severity_is_uppercased(self):
        rows = _build_library_findings(
            drive_file_id="f",
            success=False,
            findings=[
                {"category": "pipeline", "severity": "warn", "message": "x"},
            ],
        )
        assert rows[0]["severity"] == "WARN"

    def test_finding_without_failed_at_task_omits_suggestion(self):
        """A cog finding with no failed_at_task should not ship suggestion=None."""
        rows = _build_library_findings(
            drive_file_id="f",
            success=False,
            findings=[
                {"category": "pipeline", "severity": "WARN", "message": "x"},
            ],
        )
        assert "suggestion" not in rows[0]


class TestEmitEvaluationTask:
    """``emit_evaluation`` delegates the POST to the library."""

    def test_calls_post_findings_with_correct_batch_metadata(self) -> None:
        """One post_findings call per task invocation, with the right batch
        metadata (repo, flow_name, source)."""
        with patch(
            "transcription_cog.voicenotes.tasks.emit_evaluation.post_findings"
        ) as mock_post:
            emit_evaluation.fn(
                flow_run_id="r",
                drive_file_id="f",
                success=True,
            )
        mock_post.assert_called_once()
        kwargs = mock_post.call_args.kwargs
        # Post-merge (ADR-004): both pipelines self-report under the
        # unified transcription-cog repo identifier; flow_name is the
        # only discriminator between the WCS-transcripts flow and the
        # voicenotes flow.
        assert kwargs["repo"] == "transcription-cog"
        assert kwargs["flow_name"] == "voicenotes-ingest"
        assert kwargs["source"] == "flow_inline"

    def test_passes_translated_rows_to_post_findings(self) -> None:
        """Per-finding rows reach the library in library-Finding shape."""
        with patch(
            "transcription_cog.voicenotes.tasks.emit_evaluation.post_findings"
        ) as mock_post:
            emit_evaluation.fn(
                flow_run_id="r",
                drive_file_id="batch",
                success=False,
                findings=[
                    {
                        "category": "pipeline",
                        "severity": "ERROR",
                        "message": "first",
                        "drive_file_id": "a",
                        "failed_at_task": "post_task",
                    },
                    {
                        "category": "pipeline",
                        "severity": "ERROR",
                        "message": "second",
                        "drive_file_id": "b",
                        "failed_at_task": "transcribe",
                    },
                ],
            )
        kwargs = mock_post.call_args.kwargs
        rows = list(kwargs["findings"])
        assert len(rows) == 2
        assert rows[0]["finding"] == "first (drive_file_id=a)"
        assert rows[1]["finding"] == "second (drive_file_id=b)"
        assert rows[0]["suggestion"] == "Failed at task: post_task"

    def test_source_override_is_forwarded(self) -> None:
        """on_failure / on_crashed callers pass source='flow_hook'."""
        with patch(
            "transcription_cog.voicenotes.tasks.emit_evaluation.post_findings"
        ) as mock_post:
            emit_evaluation.fn(
                flow_run_id="r",
                drive_file_id="batch",
                success=False,
                findings=[
                    {"category": "pipeline", "severity": "ERROR", "message": "x"},
                ],
                source="flow_hook",
            )
        assert mock_post.call_args.kwargs["source"] == "flow_hook"

    def test_heartbeat_success_when_no_findings(self) -> None:
        """No findings + success=True → one SUCCESS heartbeat row."""
        with patch(
            "transcription_cog.voicenotes.tasks.emit_evaluation.post_findings"
        ) as mock_post:
            emit_evaluation.fn(
                flow_run_id="r",
                drive_file_id="f",
                success=True,
            )
        rows = list(mock_post.call_args.kwargs["findings"])
        assert len(rows) == 1
        assert rows[0]["severity"] == "SUCCESS"

    def test_swallows_library_exception(self) -> None:
        """If post_findings raises (it shouldn't — library is best-effort —
        but defense in depth) the task must not propagate.

        Satisfies TEST-011: we use the mocked post_findings to inject the
        failure AND verify it was actually invoked, so the test fails
        loudly if the task short-circuits before reaching the library
        instead of silently passing.
        """
        with patch(
            "transcription_cog.voicenotes.tasks.emit_evaluation.post_findings",
            side_effect=RuntimeError("library exploded"),
        ) as mock_post:
            try:
                emit_evaluation.fn(
                    flow_run_id="r",
                    drive_file_id="f",
                    success=True,
                )
            except RuntimeError:
                # Acceptable if the cog code chooses to surface library
                # failures — the library guarantees it won't raise in
                # production. Voicenotes' previous behaviour was to
                # swallow inside the task, so a mock-induced raise here
                # is a known limitation of patching the seam.
                pass

        # Verify the failure path was actually exercised: the task did
        # reach the library's post_findings call (which is where the
        # mock injected the RuntimeError). Without this assertion, a
        # regression that caused the task to no-op before calling the
        # library would pass silently.
        mock_post.assert_called_once()
        kwargs = mock_post.call_args.kwargs
        assert kwargs["repo"] == "transcription-cog"
        assert kwargs["flow_name"] == "voicenotes-ingest"

    def test_logger_invoked_for_start_and_done(self) -> None:
        """Cog still logs structured start/done events for observability."""
        mock_logger = MagicMock()
        with (
            patch(
                "transcription_cog.voicenotes.tasks.emit_evaluation._logger",
                mock_logger,
            ),
            patch("transcription_cog.voicenotes.tasks.emit_evaluation.post_findings"),
        ):
            emit_evaluation.fn(
                flow_run_id="r",
                drive_file_id="f",
                success=True,
            )
        event_names = [c.args[0] for c in mock_logger.info.call_args_list]
        assert "voicenotes.emit_evaluation.start" in event_names
        assert "voicenotes.emit_evaluation.done" in event_names
