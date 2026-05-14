"""Google Drive client wrapper.

Thin adapter on top of ``mini_app_polis.google.GoogleAPI`` (sourced
from common-python-utils). Scoped to operations this cog actually
uses; nothing more.

Why a wrapper rather than calling ``GoogleAPI.from_env().drive``
directly?

  - Single auth surface — the singleton accessor caches the
    authenticated client; tests can swap it out wholesale.
  - One place to add cog-specific structured logging on every Drive
    call. CD-009 requires this and upstream's logger doesn't yet
    structure events for us (see docs/NEEDED_FROM_COMMON.md).
  - Keeps the cog's task-layer code clean of upstream's parameter
    names (``new_parent_id`` vs ``dest_folder_id``, etc).
"""

from __future__ import annotations

from typing import Any

from notes_ingest_cog.voicenotes._shared import get_google_api, get_logger

_logger = get_logger("voicenotes-cog")


class DriveClient:
    """Drive operations needed by voicenotes-cog.

    Operations:
        - download_file(file_id) -> bytes
        - move_file(file_id, dest_folder_id) -> None
        - delete_file(file_id) -> None
        - list_files_older_than(folder_id, days) -> list[dict]
        - get_file_metadata(file_id) -> dict
        - ensure_subfolder(parent_folder_id, name) -> str
    """

    def __init__(self, *, google_api: Any | None = None) -> None:
        self._g = google_api or get_google_api()

    # ------------------------------------------------------------------
    # Operations
    # ------------------------------------------------------------------

    def download_file(self, file_id: str) -> bytes:
        """Download the audio file content."""
        _logger.info(
            "drive.download.start",
            category="api",
            context={"drive_file_id": file_id},
        )
        downloaded = self._g.drive.download_file_bytes(file_id)
        _logger.info(
            "drive.download.success",
            category="api",
            context={
                "drive_file_id": file_id,
                "bytes": len(downloaded.data),
                "name": downloaded.name,
                "mime_type": downloaded.mime_type,
            },
        )
        return downloaded.data  # type: ignore[no-any-return]

    def move_file(self, file_id: str, dest_folder_id: str) -> None:
        """Move a file to a different Drive folder.

        Idempotent at upstream's level: if it's already in the
        destination, the API still succeeds. We log the action either
        way.
        """
        _logger.info(
            "drive.move.start",
            category="api",
            context={
                "drive_file_id": file_id,
                "dest_folder_id": dest_folder_id,
            },
        )
        self._g.drive.move_file(file_id, new_parent_id=dest_folder_id)
        _logger.info(
            "drive.move.success",
            category="api",
            context={
                "drive_file_id": file_id,
                "dest_folder_id": dest_folder_id,
            },
        )

    def delete_file(self, file_id: str) -> None:
        """Permanently delete a file. Used by cleanup flow."""
        _logger.info(
            "drive.delete.start",
            category="api",
            context={"drive_file_id": file_id},
        )
        self._g.drive.delete_file(file_id)
        _logger.info(
            "drive.delete.success",
            category="api",
            context={"drive_file_id": file_id},
        )

    def list_files(self, folder_id: str) -> list[Any]:
        """List files (non-recursive) in a folder. Returns ``DriveFile`` records."""
        return list(self._g.drive.list_files(folder_id))  # type: ignore[no-any-return]

    def list_files_older_than(self, folder_id: str, days: int) -> list[Any]:
        """List files older than ``days`` (by ``modifiedTime``).

        Returns the upstream ``DriveFile`` records (id, name, mime_type,
        modified_time). Caller filters/operates on the returned list.
        """
        from datetime import UTC, datetime, timedelta

        cutoff = datetime.now(UTC) - timedelta(days=days)
        all_files = self.list_files(folder_id)
        old: list[Any] = []
        for f in all_files:
            mod = getattr(f, "modified_time", None)
            if mod is None:
                continue
            try:
                # Drive returns RFC-3339. Python 3.11+ `fromisoformat`
                # accepts a trailing `Z` natively, but normalising to
                # `+00:00` is harmless and tolerates older formats.
                ts = datetime.fromisoformat(mod.replace("Z", "+00:00"))
            except (ValueError, AttributeError):
                continue
            if ts < cutoff:
                old.append(f)
        return old

    def ensure_subfolder(self, parent_folder_id: str, name: str) -> str:
        """Get or create a subfolder. Returns its ID."""
        return self._g.drive.ensure_folder(parent_folder_id, name)  # type: ignore[no-any-return]


# Module-level singleton: lazy auth, cheap to reset.
_client: DriveClient | None = None


def get_drive_client() -> DriveClient:
    """Lazy singleton. Reset to None on auth failure to force re-auth."""
    global _client
    if _client is None:
        _client = DriveClient()
    return _client


def reset_drive_client() -> None:
    """Force re-auth on next get_drive_client() call.

    Call from auth-failure handlers.
    """
    global _client
    _client = None
