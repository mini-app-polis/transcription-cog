"""Prefect task: post evaluation findings to api-kaianolevine-com.

Per ecosystem-standards, every flow run emits one or more evaluation
records. evaluator-cog runs separately on a schedule and reads these
records to populate the pipeline-health dashboard.

Auth (CD-012): we use ``mini_app_polis.api.KaianoApiClient`` from the
shared library, which exchanges our machine-secret-key for a
short-lived Clerk M2M opaque token, caches it until ~60s before
expiry, and refreshes automatically. No long-lived bearer token
lives in env.

Schema and route reference:

  - Route: ``POST /v1/evaluations`` (router prefix ``/v1`` +
    in-router path ``/evaluations``). The endpoint accepts a single
    ``PipelineEvaluationCreate`` payload per request, NOT a batch
    of findings, so a flow run with N findings makes N POSTs.
  - Schema: ``PipelineEvaluationCreate`` uses
    ``model_config = ConfigDict(extra="forbid")`` — any unknown
    field returns 422. The cog's payload must be exactly:
    ``run_id``, ``violation_id``, ``repo``, ``dimension``,
    ``severity``, ``finding``, ``suggestion``,
    ``standards_version``, ``source``, ``flow_name``.

Failure semantics: each per-row POST is swallowed independently.
Pipeline-health is observability, not the source of truth — a flaky
api-kaianolevine-com must not turn a successful voice-note ingest
into a failure, and one failed finding-POST must not drop the
others.
"""

from __future__ import annotations

import json
from importlib.metadata import PackageNotFoundError, version
from pathlib import Path
from typing import Any

from prefect import task

from notes_ingest_cog.voicenotes._shared import (
    KaianoApiError,
    get_kaiano_api_client,
    get_logger,
)
from notes_ingest_cog.voicenotes.config import settings

_logger = get_logger("voicenotes-cog")


# Route on api-kaianolevine-com. The router is mounted at prefix
# ``/v1`` in main.py and the in-router path is ``/evaluations`` —
# NOT ``/pipeline_evaluations``. ``pipeline_evaluations`` is the
# underlying SQL table name; the HTTP path is shorter.
_EVALUATIONS_PATH = "/v1/evaluations"

# Repo identifier written into every finding row. Single source of
# truth so it can't drift between findings.
_REPO_NAME = "voicenotes-cog"

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
# ``source`` (so that the API's latest-per-(repo, source) dedup gave
# per-file freshness) had exactly that symptom. Per-file context now
# lives in the ``finding`` text; ``source`` is reserved for the
# canonical run-type marker.
_SOURCE_FLOW_INLINE = "flow_inline"  # end-of-flow emissions
_SOURCE_FLOW_HOOK = "flow_hook"  # on_failure / on_crashed emissions

# Fallback standards_version when package.json isn't on disk at
# runtime (e.g., installed wheel without it). The API schema
# defaults to "6.0" but accepts any string.
_STANDARDS_VERSION_FALLBACK = "6.0"


def _processor_version() -> str:
    """Return the cog's installed package version, or a marker."""
    try:
        return version("voicenotes-cog")
    except PackageNotFoundError:  # editable install / not installed
        return "0.0.0+local"


def _standards_version() -> str:
    """Read standards_version from package.json with a safe fallback.

    Walks parents of this file until it finds a package.json,
    returns its ``standards_version`` field. Falls back to
    ``_STANDARDS_VERSION_FALLBACK`` if the file is missing,
    unparseable, or lacks the field — the API schema accepts any
    string here so this is best-effort by design.
    """
    here = Path(__file__).resolve()
    for parent in here.parents:
        candidate = parent / "package.json"
        if candidate.is_file():
            try:
                data = json.loads(candidate.read_text(encoding="utf-8"))
            except json.JSONDecodeError:
                return _STANDARDS_VERSION_FALLBACK
            value = data.get("standards_version")
            if isinstance(value, str) and value:
                return value
            return _STANDARDS_VERSION_FALLBACK
    return _STANDARDS_VERSION_FALLBACK


def _append_drive_file_id(text: str, drive_file_id: str | None) -> str:
    """Append ``(drive_file_id=...)`` to a finding text when meaningful.

    The literal ``"batch"`` is a sentinel used by the inline body and
    failure hook for the aggregate row; appending it adds noise without
    information, so it's elided.
    """
    if not drive_file_id or drive_file_id == "batch":
        return text
    return f"{text} (drive_file_id={drive_file_id})"


def _build_finding_rows(
    *,
    flow_run_id: str,
    drive_file_id: str,
    success: bool,
    findings: list[dict[str, Any]] | None,
    source: str = _SOURCE_FLOW_INLINE,
) -> list[dict[str, Any]]:
    """Translate the cog's batch-shape input into per-row API payloads.

    Cog input:

      ``findings`` is a list of cog-shaped dicts with keys
      ``category``, ``severity``, ``message``, ``drive_file_id``,
      ``failed_at_task``.

    API output:

      One ``PipelineEvaluationCreate``-shaped dict per finding,
      with the cog's fields mapped onto the API schema fields
      (``message`` → ``finding`` with the per-finding drive_file_id
      appended for visibility, ``failed_at_task`` → ``suggestion``).
      Every row carries a canonical ``source`` value
      (``flow_inline`` or ``flow_hook``) so the Pipeline Health UI
      can group it under the correct "Run Type" facet.

    Edge cases:

      - ``findings`` is empty/None and ``success`` is True → one
        SUCCESS row so the dashboard records a heartbeat for this
        flow run.
      - ``findings`` is empty/None and ``success`` is False → one
        ERROR row (terminal-failure path; on_crashed/on_failure
        hooks land here, and pass ``source="flow_hook"``).

    Standards version is stamped onto every row.
    """
    standards_version = _standards_version()
    rows: list[dict[str, Any]] = []

    if findings:
        for f in findings:
            base_message = str(f.get("message") or f.get("finding") or "no message")
            file_id_for_finding = str(f.get("drive_file_id") or drive_file_id or "")
            rows.append(
                {
                    "run_id": flow_run_id,
                    "violation_id": None,
                    "repo": _REPO_NAME,
                    "dimension": str(f.get("category") or _DIMENSION_PIPELINE),
                    "severity": str(f.get("severity") or "ERROR").upper(),
                    "finding": _append_drive_file_id(base_message, file_id_for_finding),
                    "suggestion": (
                        f"Failed at task: {f['failed_at_task']}"
                        if f.get("failed_at_task")
                        else None
                    ),
                    "standards_version": standards_version,
                    "source": source,
                    "flow_name": _FLOW_NAME,
                }
            )
        return rows

    # No findings: emit a single heartbeat row capturing batch outcome.
    severity = "SUCCESS" if success else "ERROR"
    base_text = (
        f"voicenotes ingest completed (processor={_processor_version()})"
        if success
        else "voicenotes ingest terminal failure"
    )
    rows.append(
        {
            "run_id": flow_run_id,
            "violation_id": None,
            "repo": _REPO_NAME,
            "dimension": _DIMENSION_PIPELINE,
            "severity": severity,
            "finding": _append_drive_file_id(base_text, drive_file_id),
            "suggestion": None,
            "standards_version": standards_version,
            "source": source,
            "flow_name": _FLOW_NAME,
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

    Args:
        flow_run_id: Prefect flow run identifier — written to the
            ``run_id`` field of each row for cross-system correlation.
        drive_file_id: The voice-note source file id (or ``"batch"``
            for aggregate emissions). Folded into each row's
            ``finding`` text so per-file context stays visible in the
            Pipeline Health UI; not written to ``source``.
        success: Aggregate batch success — controls severity of the
            heartbeat row when ``findings`` is empty.
        findings: Optional list of cog-shaped finding dicts. Each
            element becomes one POST row.
        source: Canonical run-type marker per ecosystem-standards
            ``standards/evaluation.yaml``. Defaults to ``"flow_inline"``
            for end-of-flow emissions; the ``on_failure`` /
            ``on_crashed`` hooks must pass ``"flow_hook"``. The
            Pipeline Health UI's "Run Type" facet groups by this
            field — non-canonical values render as untyped and are
            hidden from every Run Type filter.

    Posting failures are logged at WARN and swallowed, per the
    "observability not source-of-truth" semantic. Per-row errors
    are isolated; one failed POST does not drop the others.
    """
    rows = _build_finding_rows(
        flow_run_id=flow_run_id,
        drive_file_id=drive_file_id,
        success=success,
        findings=findings,
        source=source,
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

    try:
        client = get_kaiano_api_client(
            base_url=settings.kaiano_api_base_url,
            machine_secret=settings.kaiano_api_clerk_machine_secret,
        )
    except Exception as exc:
        _logger.warning(
            "voicenotes.emit_evaluation.client_init_failed",
            category="api",
            context={
                "flow_run_id": flow_run_id,
                "error": str(exc),
                "error_type": type(exc).__name__,
            },
        )
        return

    posted = 0
    failed = 0
    for row in rows:
        try:
            client.post(_EVALUATIONS_PATH, row)
            posted += 1
        except KaianoApiError as exc:
            failed += 1
            _logger.warning(
                "voicenotes.emit_evaluation.failure",
                category="api",
                context={
                    "flow_run_id": flow_run_id,
                    "error": str(exc),
                    "status_code": getattr(exc, "status_code", None),
                    "row_severity": row.get("severity"),
                    "row_source": row.get("source"),
                },
            )
        except Exception as exc:
            failed += 1
            _logger.warning(
                "voicenotes.emit_evaluation.failure",
                category="api",
                context={
                    "flow_run_id": flow_run_id,
                    "error": str(exc),
                    "error_type": type(exc).__name__,
                    "row_severity": row.get("severity"),
                    "row_source": row.get("source"),
                },
            )

    _logger.info(
        "voicenotes.emit_evaluation.done",
        category="api",
        context={
            "flow_run_id": flow_run_id,
            "rows_posted": posted,
            "rows_failed": failed,
            "rows_total": len(rows),
        },
    )
