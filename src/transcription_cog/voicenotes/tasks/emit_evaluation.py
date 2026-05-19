"""Prefect task: post evaluation findings to api-kaianolevine-com.

Per ecosystem-standards, every flow run emits one or more evaluation
records. evaluator-cog runs separately on a schedule and reads these
records to populate the pipeline-health dashboard.

This task is a **thin cog-specific adapter** on top of
:func:`mini_app_polis.pipeline_status.post_findings`. The shared library
owns the payload shape, the Clerk M2M auth, the per-row error isolation,
and the best-effort semantics. The voicenotes flow's only job in this
file is to translate the cog's batch-shape input (a list of dicts with
``category``, ``severity``, ``message``, ``drive_file_id``,
``failed_at_task``) into the shape ``post_findings`` accepts.

Failure semantics: posting failures are swallowed by the library (with
per-row isolation — one failed POST does not drop the others). Pipeline-
health is observability, not the source of truth — a flaky
api-kaianolevine-com must not turn a successful voice-note ingest into
a failure.
"""

from __future__ import annotations

from importlib.metadata import PackageNotFoundError, version
from typing import Any

from mini_app_polis.pipeline_status import post_findings
from prefect import task

from transcription_cog.voicenotes._shared import get_logger
from transcription_cog.voicenotes.config import settings

_logger = get_logger("voicenotes-cog")


# Repo identifier written into every finding row. Single source of
# truth so it can't drift between findings.
#
# Both pipelines in this repo (WCS transcripts via flow.py and
# voicenotes via voicenotes/flows/) self-report under the same
# ``transcription-cog`` repo label since the May-2026 merge from the
# old ``notes-ingest-cog`` and ``voicenotes-cog`` standalone repos
# (docs/decisions/ADR-004-voicenotes-merge.md). The two pipelines stay
# distinguishable in the Pipeline Health UI via ``flow_name`` —
# ``process-transcript`` for WCS transcripts, ``voicenotes-ingest``
# for voicenotes — not via a per-pipeline repo facet.
_REPO_NAME = "transcription-cog"

# Flow name written into every finding row. Matches the ``@flow``
# decorator name in flows/ingest.py — keep these in sync.
_FLOW_NAME = "voicenotes-ingest"

# Default dimension for runtime ingest findings. ecosystem-standards
# defines several dimensions (structural_conformance,
# pipeline_consistency, testing_coverage, ...); ``pipeline_consistency``
# is the closest fit for runtime flow execution health.
_DIMENSION_PIPELINE = "pipeline_consistency"

# Canonical ``source`` values per ecosystem-standards
# ``standards/evaluation.yaml``. The Pipeline Health UI derives its
# "Run Type" facet from this field — rows with non-canonical source
# values are silently hidden from every Run Type bucket (only visible
# under "All"). The previous practice of stuffing drive_file_id into
# ``source`` had exactly that symptom. Per-file context now lives in
# the ``finding`` text; ``source`` is reserved for the canonical
# run-type marker.
_SOURCE_FLOW_INLINE = "flow_inline"  # end-of-flow emissions
_SOURCE_FLOW_HOOK = "flow_hook"  # on_failure / on_crashed emissions


def _processor_version() -> str | None:
    """Return the cog's installed package version, or ``None``.

    Returns ``None`` (rather than a marker like ``"0.0.0+local"``) when
    the package isn't installed under the expected distribution name so
    the caller can elide the suffix entirely. Pipeline Health rows
    previously displayed ``(processor=0.0.0+local)`` on every emission
    because this lookup used the pre-merge distribution name
    ``"voicenotes-cog"`` after the May-2026 merge into
    ``transcription-cog`` (ADR-004) — every prod call fell through to
    the fallback and stamped the marker onto otherwise-clean findings.
    Query the post-merge distribution name and treat absence as
    "don't append anything", not "append a marker".
    """
    try:
        return version("transcription-cog")
    except PackageNotFoundError:  # editable install / not installed
        return None


def _append_drive_file_id(text: str, drive_file_id: str | None) -> str:
    """Append ``(drive_file_id=...)`` to a finding text when meaningful.

    The literal ``"batch"`` is a sentinel used by the inline body and
    failure hook for the aggregate row; appending it adds noise without
    information, so it's elided.
    """
    if not drive_file_id or drive_file_id == "batch":
        return text
    return f"{text} (drive_file_id={drive_file_id})"


def _build_library_findings(
    *,
    drive_file_id: str,
    success: bool,
    findings: list[dict[str, Any]] | None,
) -> list[dict[str, Any]]:
    """Translate the cog's batch-shape input into library Finding rows.

    Cog input:

      ``findings`` is a list of cog-shaped dicts with keys
      ``category``, ``severity``, ``message``, ``drive_file_id``,
      ``failed_at_task``.

    Library output:

      One :class:`mini_app_polis.pipeline_status.Finding` per cog finding,
      with the cog's fields mapped onto the library schema
      (``message`` → ``finding`` with the per-finding drive_file_id
      appended for visibility, ``failed_at_task`` → ``suggestion``).
      Per-row ``category`` overrides the default ``pipeline_consistency``
      dimension.

    Edge cases:

      - ``findings`` is empty/None and ``success`` is True → one
        SUCCESS row so the dashboard records a heartbeat for this
        flow run.
      - ``findings`` is empty/None and ``success`` is False → one
        ERROR row (terminal-failure path; on_crashed/on_failure
        hooks land here).
    """
    rows: list[dict[str, Any]] = []

    if findings:
        for f in findings:
            base_message = str(f.get("message") or f.get("finding") or "no message")
            file_id_for_finding = str(f.get("drive_file_id") or drive_file_id or "")
            row: dict[str, Any] = {
                "severity": str(f.get("severity") or "ERROR").upper(),
                "finding": _append_drive_file_id(base_message, file_id_for_finding),
                "dimension": str(f.get("category") or _DIMENSION_PIPELINE),
            }
            if f.get("failed_at_task"):
                row["suggestion"] = f"Failed at task: {f['failed_at_task']}"
            rows.append(row)
        return rows

    # No findings: emit a single heartbeat row capturing batch outcome.
    #
    # SUCCESS heartbeats include the resolved processor version when
    # ``importlib.metadata`` can find the installed ``transcription-cog``
    # distribution, so the Pipeline Health UI shows e.g.
    # ``voicenotes ingest completed (processor=1.10.2)`` and operators
    # can correlate a heartbeat with the build that emitted it. When the
    # package isn't installed (editable dev checkouts, ad-hoc
    # invocations) ``_processor_version`` returns ``None`` and the
    # suffix is omitted — better silence than the old
    # ``(processor=0.0.0+local)`` marker noise.
    severity = "SUCCESS" if success else "ERROR"
    if success:
        processor = _processor_version()
        base_text = (
            f"voicenotes ingest completed (processor={processor})"
            if processor
            else "voicenotes ingest completed"
        )
    else:
        base_text = "voicenotes ingest terminal failure"
    rows.append(
        {
            "severity": severity,
            "finding": _append_drive_file_id(base_text, drive_file_id),
            "dimension": _DIMENSION_PIPELINE,
        }
    )
    return rows


@task(
    name="emit_evaluation",
    retries=settings.extract_task_retries,
    retry_delay_seconds=settings.extract_task_retry_delays_seconds,
)
def emit_evaluation(
    flow_run_id: str,
    drive_file_id: str,
    success: bool,
    findings: list[dict[str, Any]] | None = None,
    source: str = _SOURCE_FLOW_INLINE,
) -> None:
    """POST one or more evaluation rows to api-kaianolevine-com.

    Delegates the actual HTTP/auth/best-effort plumbing to
    :func:`mini_app_polis.pipeline_status.post_findings`; this task
    owns only the cog-specific translation from the cog's finding
    dict shape to the library's :class:`Finding` shape.

    Args:
        flow_run_id: Prefect flow run identifier. Forwarded to the
            library, which still resolves its own run_id from the
            Prefect runtime context but accepts this for callers that
            already have it in hand.
        drive_file_id: The voice-note source file id (or ``"batch"``
            for aggregate emissions). Folded into each row's
            ``finding`` text so per-file context stays visible in the
            Pipeline Health UI; not written to ``source``.
        success: Aggregate batch success — controls severity of the
            heartbeat row when ``findings`` is empty.
        findings: Optional list of cog-shaped finding dicts. Each
            element becomes one library Finding row.
        source: Canonical run-type marker per ecosystem-standards
            ``standards/evaluation.yaml``. Defaults to ``"flow_inline"``
            for end-of-flow emissions; the ``on_failure`` /
            ``on_crashed`` hooks must pass ``"flow_hook"``.

    Posting failures are logged at WARN by the library and swallowed,
    per the "observability not source-of-truth" semantic. Per-row
    errors are isolated; one failed POST does not drop the others.
    """
    # The flow_run_id arg is kept on the public signature for
    # backwards compatibility with existing call sites. The library
    # resolves run_id itself from the Prefect runtime / env, so we
    # only need to log it locally for observability.
    rows = _build_library_findings(
        drive_file_id=drive_file_id,
        success=success,
        findings=findings,
    )

    _logger.info(
        "voicenotes.emit_evaluation.start",
        category="api",
        context={
            "flow_run_id": flow_run_id,
            "success": success,
            "row_count": len(rows),
        },
    )

    post_findings(
        repo=_REPO_NAME,
        flow_name=_FLOW_NAME,
        findings=rows,
        source=source,
    )

    _logger.info(
        "voicenotes.emit_evaluation.done",
        category="api",
        context={
            "flow_run_id": flow_run_id,
            "row_count": len(rows),
        },
    )
