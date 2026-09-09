"""Cleanup flow: trash archived audio older than retention window.

Trigger model: this flow runs at the end of every ``voicenotes_ingest``
invocation (see ``flows/ingest.py``) — there is no cron schedule.
Cleanup-mode dispatch via the router is preserved so an operator can
still trigger a manual sweep from the Prefect UI if needed.

That coupling is deliberate, and it has a consequence worth stating
plainly rather than discovering: **retention advances with use.**
``voicenotes_ingest`` only runs when watcher-cog sees a change in
``voice-inbox/``, so a stretch with no new recordings is also a stretch
with no sweep, and archived audio can sit past
``ARCHIVE_RETENTION_DAYS`` for as long as that lasts. The window is
therefore a floor on how long audio is kept, not a ceiling. Deleting
promptly was never the point — bounding how much accumulates was — and
a scheduled sweep was considered and rejected rather than overlooked.
An operator who wants the archive drained during a quiet stretch runs
``voicenotes-cleanup`` from the Prefect UI.

The flow walks ``voice-inbox/processed/`` and trashes archived audio
older than ``ARCHIVE_RETENTION_DAYS`` days.

Trash, not delete
-----------------
This used to call ``files.delete`` and never once succeeded: every
attempt returned 404 while the archive step's moves worked perfectly.
``files.delete`` is Drive's irreversible path and requires the
*organizer* role on a shared drive — the Manager access level. This
cog's service account is a Content manager, which can add, move and
trash content but cannot permanently delete it. Google's own help pages
describe Content manager as able to "delete" shared drive content, but
their permissions table separates the two rows: trashing is Manager and
Content manager, permanently deleting from the trash is Manager only.

Trashing needs no new privilege, and the shared drive empties its own
trash after 30 days, so storage is still reclaimed on its own. The
retention window is therefore a floor of ``ARCHIVE_RETENTION_DAYS`` in
``processed/`` plus roughly 30 more in the trash — and a bug in this
sweep costs a restore rather than the recording. Each immediate child of
``processed/`` is a date-bucket folder — new archives go under per-day
``YYYY-MM-DD/`` folders, and legacy monthly ``YYYY-MM/`` folders from
before that change also remain in the archive.

Which clock retention runs on
-----------------------------
The bucket folder name is the archive date, so it is what retention is
measured against. This flow used to ignore the name and filter on each
file's Drive ``modifiedTime`` instead, which is a property of the audio
— roughly when it was recorded — not of when it was archived. The
setting promises "days to keep audio in processed/", and those are only
the same clock when a note is processed the day it is recorded. A voice
note sitting in the inbox for three weeks before a run picked it up
could satisfy the retention test the moment it landed, and be deleted on
the very next sweep.

Per-day buckets are therefore drained whole once the bucket's own date
falls outside the window — every file in a ``YYYY-MM-DD/`` folder was
archived that day, so no per-file timestamp is needed or trusted.
Legacy monthly buckets carry no single archive date, so they keep the
old per-file ``modifiedTime`` filter; they only shrink, so the
imprecision ages out on its own.

A per-day bucket is trashed once it has been drained, so the archive
shows only dates that still hold audio rather than accumulating empty
folders forever. Trashing a folder is an edit like trashing a file, so
it needs no privilege the sweep does not already have — which is why
this became possible only once the sweep stopped trying to delete.

A bucket is trashed only when every file in it was trashed
successfully: a partial drain leaves the folder alone so nothing is
hidden while a file is still sitting in it. Legacy monthly buckets are
never trashed at all — they hold files from a whole month, and the
per-file filter that drains them can leave recent recordings behind.

Failure mode: a single delete failure does not stop the batch; we
log and continue, then report counts at the end. The per-deletion
log line is the audit trail. The ingest flow that invokes us also
swallows our exceptions — cleanup is best-effort housekeeping, not
on the success path.

Best-effort is not the same as unreported, and this flow used to
conflate the two. It logged ``voicenotes.cleanup.success`` with the
counts interpolated into it, so a sweep that attempted 38 deletions
and completed none announced itself as a success — the same shape as
the September 2026 finding-delivery outage, where three instruments
reported green while nothing landed. The final log line now takes its
level from the outcome, and the batch returns ``attempted`` so the
caller can tell "nothing to do" apart from "nothing worked".
"""

from __future__ import annotations

import logging
from datetime import UTC, date, datetime, timedelta
from typing import Any

from prefect import flow, get_run_logger
from prefect.concurrency.sync import concurrency

from transcription_cog.voicenotes._shared import get_logger
from transcription_cog.voicenotes.clients.drive_client import get_drive_client
from transcription_cog.voicenotes.config import require_voicenotes_settings, settings

_logger = get_logger("voicenotes-cog")
_log = logging.getLogger(__name__)

_PROCESSED_FOLDER_NAME = "processed"

# Drive's folder mime type. Children of ``processed/`` are expected to
# be date-bucket folders, but nothing stops a stray file landing there,
# and listing children of a *file* id returns an empty list rather than
# an error — so an unfiltered walk counts that stray as a scanned
# bucket and reports a sweep that never looked at anything.
_DRIVE_FOLDER_MIME = "application/vnd.google-apps.folder"

#: Per-day archive bucket name, e.g. ``2026-09-09``. The name is written
#: by ``tasks/archive.py`` at archive time, which is exactly the fact
#: retention needs.
_BUCKET_DATE_FORMAT = "%Y-%m-%d"


def _bucket_archive_date(name: str | None) -> date | None:
    """Return the archive date encoded in a bucket folder name, or None.

    ``None`` means "this bucket does not carry a single archive date" —
    a legacy monthly ``YYYY-MM/`` folder, or something an operator
    created by hand. Callers fall back to per-file timestamps for those.
    """
    if not name:
        return None
    try:
        return datetime.strptime(name.strip(), _BUCKET_DATE_FORMAT).date()
    except ValueError:
        return None


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
    """Trash processed audio older than ``ARCHIVE_RETENTION_DAYS``.

    Returns a summary dict: ``{trashed, failed, attempted,
    buckets_trashed, buckets_failed, retention_days,
    date_folders_scanned}``.

    ``attempted`` is what makes the other two readable: ``trashed=0,
    failed=0`` is an idle sweep with nothing past retention, while
    ``trashed=0, failed=38`` is a sweep that is completely broken. The
    counts alone cannot distinguish them, which is why the summary log
    line below is chosen from the outcome rather than fixed at INFO.
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

        trashed = 0
        failed = 0
        buckets_trashed = 0
        buckets_failed = 0
        date_folders_scanned = 0
        first_error: str | None = None
        # Buckets dated on or after this are still inside the window.
        cutoff_date = datetime.now(UTC).date() - timedelta(days=retention_days)

        # Each immediate child of processed/ is a date bucket folder.
        # New archives use ``YYYY-MM-DD/`` (per processing day);
        # legacy archives use ``YYYY-MM/`` (per month) from before the
        # daily-bucket change. Don't parse the folder name — just walk
        # every immediate child so both layouts drain uniformly.
        for date_folder in drive.list_files(processed_root_id):
            date_folder_id = getattr(date_folder, "id", None)
            if not date_folder_id:
                continue
            if getattr(date_folder, "mime_type", None) != _DRIVE_FOLDER_MIME:
                # A stray file directly under processed/. Listing its
                # children would return [] and silently inflate the
                # scanned-bucket count.
                _logger.warning(
                    "voicenotes.cleanup.unexpected_file_in_processed_root",
                    category="data",
                    context={
                        "drive_file_id": date_folder_id,
                        "name": getattr(date_folder, "name", None),
                    },
                )
                continue
            date_folders_scanned += 1

            bucket_date = _bucket_archive_date(getattr(date_folder, "name", None))
            if bucket_date is not None:
                if bucket_date >= cutoff_date:
                    # Whole bucket is still within retention.
                    continue
                # Everything here was archived on bucket_date.
                expired_files = drive.list_files(date_folder_id)
            else:
                # Legacy monthly bucket (or a hand-made folder): no single
                # archive date, so fall back to per-file modifiedTime.
                _logger.info(
                    "voicenotes.cleanup.undated_bucket",
                    category="data",
                    context={
                        "drive_file_id": date_folder_id,
                        "name": getattr(date_folder, "name", None),
                    },
                )
                expired_files = drive.list_files_older_than(
                    date_folder_id, days=retention_days
                )

            bucket_failed = 0
            for old_file in expired_files:
                file_id = getattr(old_file, "id", None)
                if not file_id:
                    continue
                try:
                    drive.trash_file(file_id)
                    trashed += 1
                    _logger.info(
                        "voicenotes.cleanup.trashed",
                        category="pipeline",
                        context={
                            "drive_file_id": file_id,
                            "name": getattr(old_file, "name", None),
                            "date_folder": getattr(date_folder, "name", None),
                        },
                    )
                except Exception as exc:
                    failed += 1
                    bucket_failed += 1
                    if first_error is None:
                        first_error = f"{type(exc).__name__}: {exc}"
                    _logger.warning(
                        "voicenotes.cleanup.trash_failed",
                        category="pipeline",
                        context={
                            "drive_file_id": file_id,
                            "error": str(exc),
                            "error_type": type(exc).__name__,
                        },
                    )

            # The bucket is drained; retire the folder with it. Only for
            # dated buckets, and only on a clean drain — a folder still
            # holding a file that would not trash must stay visible.
            if bucket_date is not None and bucket_failed == 0:
                try:
                    drive.trash_file(date_folder_id)
                    buckets_trashed += 1
                    _logger.info(
                        "voicenotes.cleanup.bucket_trashed",
                        category="pipeline",
                        context={
                            "drive_file_id": date_folder_id,
                            "name": getattr(date_folder, "name", None),
                            "files_trashed": len(expired_files),
                        },
                    )
                except Exception as exc:
                    buckets_failed += 1
                    if first_error is None:
                        first_error = f"{type(exc).__name__}: {exc}"
                    _logger.warning(
                        "voicenotes.cleanup.bucket_trash_failed",
                        category="pipeline",
                        context={
                            "drive_file_id": date_folder_id,
                            "name": getattr(date_folder, "name", None),
                            "error": str(exc),
                            "error_type": type(exc).__name__,
                        },
                    )

    attempted = trashed + failed
    summary = {
        "trashed": trashed,
        "buckets_trashed": buckets_trashed,
        "buckets_failed": buckets_failed,
        "failed": failed,
        "attempted": attempted,
        "retention_days": retention_days,
        "date_folders_scanned": date_folders_scanned,
        "first_error": first_error,
        # Surfaced so the caller can say what was trashed, not just how
        # much: "trashed 30 recordings archived before 2026-08-26" is a
        # sentence an operator can check against the archive.
        "cutoff_date": cutoff_date.isoformat(),
    }

    # Level follows the outcome. Every file failing is not a degraded
    # sweep, it is a sweep that does not work, and it will keep not
    # working every cycle until someone looks — so it is an ERROR even
    # though cleanup is best-effort and never fails the ingest. That is
    # the case that ran undetected for months behind an INFO line
    # reading "success".
    if failed == 0 and buckets_failed == 0:
        _logger.info(
            "voicenotes.cleanup.batch_complete", category="pipeline", context=summary
        )
        flow_logger.info(
            f"voicenotes.cleanup.success trashed={trashed} failed=0 "
            f"buckets_trashed={buckets_trashed} "
            f"date_folders={date_folders_scanned}"
        )
    elif attempted > 0 and trashed == 0:
        _logger.error(
            "voicenotes.cleanup.batch_failed", category="pipeline", context=summary
        )
        flow_logger.error(
            f"voicenotes.cleanup.failure trashed=0 failed={failed} "
            f"date_folders={date_folders_scanned} first_error={first_error!r}"
        )
    else:
        _logger.warning(
            "voicenotes.cleanup.batch_degraded", category="pipeline", context=summary
        )
        flow_logger.warning(
            f"voicenotes.cleanup.degraded trashed={trashed} failed={failed} "
            f"buckets_trashed={buckets_trashed} buckets_failed={buckets_failed} "
            f"date_folders={date_folders_scanned} first_error={first_error!r}"
        )

    return summary
