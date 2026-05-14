"""Tests for emit_evaluation task.

Covers:

  - ``_build_finding_rows`` payload shape: required fields present,
    cog → API field renames, heartbeat row when no findings.
  - The Prefect task body: posts to ``/v1/evaluations`` (one POST per
    row), continues across per-row failures, and never re-raises.
"""

from __future__ import annotations

from unittest.mock import MagicMock

from notes_ingest_cog.voicenotes import _shared
from notes_ingest_cog.voicenotes.tasks import emit_evaluation as eval_mod
from notes_ingest_cog.voicenotes.tasks.emit_evaluation import (
    _build_finding_rows,
    emit_evaluation,
)

_REQUIRED_FIELDS = (
    "run_id",
    "violation_id",
    "repo",
    "dimension",
    "severity",
    "finding",
    "suggestion",
    "standards_version",
    "source",
    "flow_name",
)


class TestBuildFindingRows:
    """_build_finding_rows assembles per-row payloads matching the API schema."""

    def test_success_with_no_findings_emits_single_success_row(self):
        """A clean batch with no findings → one heartbeat row, severity SUCCESS."""
        rows = _build_finding_rows(
            flow_run_id="run-1",
            drive_file_id="batch",
            success=True,
            findings=None,
        )
        assert len(rows) == 1
        row = rows[0]
        assert row["severity"] == "SUCCESS"
        assert row["repo"] == "voicenotes-cog"
        assert row["flow_name"] == "voicenotes-ingest"
        assert row["run_id"] == "run-1"
        # Source must be a canonical run-type marker per
        # ecosystem-standards/standards/evaluation.yaml. Default for
        # end-of-flow emissions is "flow_inline" — anything else (e.g.
        # the drive_file_id, which used to live here) hides the row
        # from the Pipeline Health UI's Run Type facet.
        assert row["source"] == "flow_inline"

    def test_failure_with_no_findings_emits_single_error_row(self):
        """A terminal failure with no findings → one ERROR heartbeat row.

        Used by the ``on_crashed`` / ``on_failure`` hook path where
        the flow died before per-file findings could be collected.
        That call site passes ``source="flow_hook"``.
        """
        rows = _build_finding_rows(
            flow_run_id="run-1",
            drive_file_id="batch",
            success=False,
            findings=None,
            source="flow_hook",
        )
        assert len(rows) == 1
        assert rows[0]["severity"] == "ERROR"
        assert rows[0]["source"] == "flow_hook"

    def test_findings_become_one_row_each(self):
        """Each finding in the input list becomes its own API row."""
        rows = _build_finding_rows(
            flow_run_id="run-1",
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
        # Per-file drive_file_id is folded into ``finding`` text (not
        # source), so the row stays visible under the canonical
        # "Pipeline Eval" Run Type facet but per-file context is still
        # readable in the dashboard.
        assert rows[0]["source"] == "flow_inline"
        assert rows[1]["source"] == "flow_inline"
        assert rows[0]["finding"] == "post_task failed: 400 (drive_file_id=drive-A)"
        assert (
            rows[1]["finding"] == "transcribe failed: timeout (drive_file_id=drive-B)"
        )
        assert rows[0]["suggestion"] == "Failed at task: post_task"
        assert rows[1]["suggestion"] == "Failed at task: transcribe"

    def test_drive_file_id_batch_is_elided_from_finding_text(self):
        """The literal ``"batch"`` sentinel is dropped, not appended.

        Inline-body and failure-hook callers pass ``drive_file_id="batch"``
        for the aggregate row; appending ``(drive_file_id=batch)`` would
        be noise without information.
        """
        rows = _build_finding_rows(
            flow_run_id="run-1",
            drive_file_id="batch",
            success=True,
            findings=None,
        )
        assert "drive_file_id=" not in rows[0]["finding"]

    def test_source_defaults_to_flow_inline_and_can_be_overridden(self):
        """Default is flow_inline; failure-hook caller must pass flow_hook."""
        default = _build_finding_rows(
            flow_run_id="r",
            drive_file_id="batch",
            success=True,
            findings=None,
        )
        assert default[0]["source"] == "flow_inline"

        hook = _build_finding_rows(
            flow_run_id="r",
            drive_file_id="batch",
            success=False,
            findings=None,
            source="flow_hook",
        )
        assert hook[0]["source"] == "flow_hook"

    def test_rows_only_contain_api_schema_fields(self):
        """Output rows must contain ONLY API schema fields.

        ``PipelineEvaluationCreate`` uses ``ConfigDict(extra="forbid")``;
        any unknown field returns 422. Guard against accidentally
        re-introducing legacy fields like ``processor_version``,
        ``success``, ``context``, or ``findings``.
        """
        rows = _build_finding_rows(
            flow_run_id="r",
            drive_file_id="f",
            success=True,
            findings=None,
        )
        forbidden = {
            "processor_version",
            "success",
            "context",
            "findings",
            "repo_name",
            "flow_run_id",
        }
        for row in rows:
            assert set(row.keys()) <= set(_REQUIRED_FIELDS)
            assert not (set(row.keys()) & forbidden)

    def test_severity_is_uppercased(self):
        """Severity strings are normalized to upper case for the API."""
        rows = _build_finding_rows(
            flow_run_id="r",
            drive_file_id="f",
            success=False,
            findings=[
                {"category": "pipeline", "severity": "warn", "message": "x"},
            ],
        )
        assert rows[0]["severity"] == "WARN"


class TestEmitEvaluationTask:
    """End-to-end coverage of the emit_evaluation Prefect task body."""

    def test_posts_one_row_per_finding(self, monkeypatch):
        """N findings → N POSTs, each at /v1/evaluations."""
        captured: list[tuple[str, dict]] = []
        fake_client = MagicMock()

        def fake_post(path, payload):
            """Capture each POST so assertions can read them back."""
            captured.append((path, payload))
            return {"ok": True}

        fake_client.post.side_effect = fake_post
        monkeypatch.setattr(eval_mod, "get_kaiano_api_client", lambda **kw: fake_client)

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
        assert len(captured) == 2
        assert all(path == "/v1/evaluations" for path, _ in captured)
        # Per-finding drive_file_id is folded into ``finding`` text now
        # that ``source`` is reserved for the canonical run-type marker.
        assert captured[0][1]["finding"] == "first (drive_file_id=a)"
        assert captured[1][1]["finding"] == "second (drive_file_id=b)"
        assert all(p["source"] == "flow_inline" for _, p in captured)

    def test_per_row_failure_does_not_drop_subsequent_rows(self, monkeypatch):
        """If one POST fails, the next is still attempted."""
        attempts: list[dict] = []
        fake_client = MagicMock()

        def fake_post(path, payload):
            """Fail the first POST, succeed the second."""
            attempts.append(payload)
            if len(attempts) == 1:
                raise RuntimeError("boom")
            return {"ok": True}

        fake_client.post.side_effect = fake_post
        monkeypatch.setattr(eval_mod, "get_kaiano_api_client", lambda **kw: fake_client)

        emit_evaluation.fn(
            flow_run_id="r",
            drive_file_id="batch",
            success=False,
            findings=[
                {"category": "pipeline", "severity": "ERROR", "message": "1"},
                {"category": "pipeline", "severity": "ERROR", "message": "2"},
            ],
        )
        assert len(attempts) == 2

    def test_swallows_kaiano_api_errors(self, monkeypatch):
        """KaianoApiError on POST is logged and swallowed (no raise)."""
        fake_client = MagicMock()
        try:
            err = _shared.KaianoApiError(
                status_code=503,
                message="upstream down",
                path="/v1/evaluations",
            )
        except TypeError:  # fallback path: KaianoApiError is plain Exception
            err = _shared.KaianoApiError("upstream down")
        fake_client.post.side_effect = err

        monkeypatch.setattr(eval_mod, "get_kaiano_api_client", lambda **kw: fake_client)

        emit_evaluation.fn(
            flow_run_id="r",
            drive_file_id="f",
            success=True,
        )
        # Heartbeat row → exactly one POST attempted, even on failure.
        fake_client.post.assert_called_once()

    def test_swallows_unexpected_errors(self, monkeypatch):
        """A non-Kaiano RuntimeError on POST is also swallowed (no raise)."""
        fake_client = MagicMock()
        fake_client.post.side_effect = RuntimeError("nope")
        monkeypatch.setattr(eval_mod, "get_kaiano_api_client", lambda **kw: fake_client)

        emit_evaluation.fn(
            flow_run_id="r",
            drive_file_id="f",
            success=True,
        )
        fake_client.post.assert_called_once()

    def test_swallows_client_init_failure(self, monkeypatch):
        """If get_kaiano_api_client raises, no POSTs run and no exception escapes.

        The client init path can fail when the Clerk machine secret
        is misconfigured at deploy. Pipeline-health is observability,
        not source-of-truth — never let this turn a successful flow
        into a failure.
        """

        def boom(**kw):
            """Simulate failure to construct the API client."""
            raise RuntimeError("missing clerk secret")

        monkeypatch.setattr(eval_mod, "get_kaiano_api_client", boom)

        # Should not raise.
        emit_evaluation.fn(
            flow_run_id="r",
            drive_file_id="f",
            success=True,
        )

    def test_posts_to_evaluations_endpoint_with_correct_repo(self, monkeypatch):
        """POST hits /v1/evaluations with repo='voicenotes-cog'."""
        captured: dict = {}
        fake_client = MagicMock()

        def fake_post(path, payload):
            """Capture the single heartbeat POST for shape assertions."""
            captured["path"] = path
            captured["payload"] = payload
            return {"ok": True}

        fake_client.post.side_effect = fake_post
        monkeypatch.setattr(eval_mod, "get_kaiano_api_client", lambda **kw: fake_client)

        emit_evaluation.fn(
            flow_run_id="r",
            drive_file_id="f",
            success=True,
        )
        fake_client.post.assert_called_once()
        assert captured["path"] == "/v1/evaluations"
        assert captured["payload"]["repo"] == "voicenotes-cog"
        assert captured["payload"]["run_id"] == "r"
        assert captured["payload"]["severity"] == "SUCCESS"
        # Heartbeat row uses the canonical end-of-flow source so the
        # Pipeline Health UI groups it under "Pipeline Eval".
        assert captured["payload"]["source"] == "flow_inline"
        # The ``"f"`` drive_file_id is non-sentinel, so it should
        # appear in the finding text — keeps per-file context visible
        # while leaving ``source`` for the run-type marker.
        assert "drive_file_id=f" in captured["payload"]["finding"]
