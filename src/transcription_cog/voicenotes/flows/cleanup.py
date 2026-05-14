"""Cleanup flow: delete archived audio older than retention window.

Trigger model: this flow runs at the end of every ``voicenotes_ingest``
invocation (see ``flows/ingest.py``) — there is no cron schedule.
Cleanup-mode dispatch via the router is preserved so an operator can
still trigger a manual sweep from the Prefect UI if needed.

The flow walks ``voice-inbox/processed/`` (one folder per YYYY-MM
month bucket) and deletes any file whose Drive ``modifiedTime`` is
older than ``ARCHIVE_RETENTION_DAYS`` days. Empty month-folders are
left in place — they are cheap and useful for browsing the archive
chronologically.

Failure mode: a single delete failure does not stop the batch; we
log and continue, then report counts at the end. The per-deletion
log line is the audit trail. The ingest flow that invokes us also
swallows our exceptions — cleanup is best-effort housekeeping, not
on the success path.
"""

from __future__ import annotations

import logging
from typing import Any

from prefect import flow, get_run_logger
from prefect.concurrency.sync import concurrency

from transcription_cog.voicenotes._shared import get_logger
from transcription_cog.voicenotes.clients.drive_client import get_drive_client
from transcription_cog.voicenotes.config import require_voicenotes_settings, settings

_logger = get_logger("voicenotes-cog")
_log = logging.getLogger(__name__)

_PROCESSED_FOLDER_NAME = "processed"

# Concurrency slot for cleanup. By design, the matching limit is
# NOT created in Prefect Cloud — Prefect's ``concurrency()``
# defaults to ``strict=False``, which means a missing limit
# silently no-ops with a warning log. We rely on that: cleanup
# overlaps are harmless (delete-by-modified-time is idempotent in
# Drive), so PIPE-009-style mutex protection isn't worth a
# precious deployment-tier slot. If a future change makes
# cleanup overlap unsafe, create the limit in Prefect Cloud and
# this code will start enforcing it without a code change.
_CLEANUP_CONCURRENCY_SLOT = "voicenotes-cog-cleanup"


def _flow_logger():
    """PIPE-006 dual-logger: Prefect run logger inside flow, stdlib outside."""
    try:
        return get_run_logger()
    except Exception:
        return _log


@flow(name="voicenotes-cleanup")
def voicenotes_cleanup() -> dict[str, Any]:
    """Delete processed audio older than ``ARCHIVE_RETENTION_DAYS``.

    Returns a summary dict: ``{deleted, failed, retention_days,
    month_folders_scanned}``.
    """
    # Fail fast and loud if voicenotes config is missing. Module import
    # accepts empty defaults so the parent cog can boot without
    # voicenotes secrets — see voicenotes.config docstring and ADR-004.
    require_voicenotes_settings()

    flow_logger = _flow_logger()
    retention_days = settings.archive_retention_days
    flow_logger.info(f"voicenotes.cleanup.start retention_days={retention_days}")

    drive = get_drive_client()
    inbox_id = settings.google_drive_voice_inbox_folder_id

    # Try to acquire the concurrency slot — by design, no matching
    # limit exists in Prefect Cloud, so this is a deliberate no-op
    # (Prefect's strict=False default just warns and proceeds). See
    # the ``_CLEANUP_CONCURRENCY_SLOT`` comment above. If the limit
    # is created later, this same code starts enforcing it.
    with concurrency(_CLEANUP_CONCURRENCY_SLOT, occupy=1):
        # Locate (or skip) the processed/ root.
        try:
            processed_root_id = drive.ensure_subfolder(inbox_id, _PROCESSED_FOLDER_NAME)
        except Exception as exc:
            _logger.error(
                "voicenotes.cleanup.failure",
                category="pipeline",
                context={"error": str(exc), "stage": "ensure_processed"},
            )
            raise

        deleted = 0
        failed = 0
        month_folders_scanned = 0

        # Each immediate child of processed/ is a YYYY-MM folder.
        for month_folder in drive.list_files(processed_root_id):
            month_folders_scanned += 1
            month_id = getattr(month_folder, "id", None)
            if not month_id:
                continue
            for old_file in drive.list_files_older_than(month_id, days=retention_days):
                file_id = getattr(old_file, "id", None)
                if not file_id:
                    continue
                try:
                    drive.delete_file(file_id)
                    deleted += 1
                    _logger.info(
                        "voicenotes.cleanup.deleted",
                        category="pipeline",
                        context={
                            "drive_file_id": file_id,
                            "name": getattr(old_file, "name", None),
                            "month_folder": getattr(month_folder, "name", None),
                        },
                    )
                except Exception as exc:
                    failed += 1
                    _logger.warning(
                        "voicenotes.cleanup.delete_failed",
                        category="pipeline",
                        context={
                            "drive_file_id": file_id,
                            "error": str(exc),
                        },
                    )

    summary = {
        "deleted": deleted,
        "failed": failed,
        "retention_days": retention_days,
        "month_folders_scanned": month_folders_scanned,
    }
    _logger.info(
        "voicenotes.cleanup.batch_complete",
        category="pipeline",
        context=summary,
    )
    flow_logger.info(
        f"voicenotes.cleanup.success deleted={deleted} failed={failed} "
        f"month_folders={month_folders_scanned}"
    )

    return summary
