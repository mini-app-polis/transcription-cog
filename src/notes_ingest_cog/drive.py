"""Google Drive helpers for notes-ingest-cog.

Thin wrappers around mini_app_polis.google.GoogleAPI that handle the
specific operations this cog needs: reading transcript text and archiving
processed files.
"""

from __future__ import annotations

from mini_app_polis import logger as log
from mini_app_polis.google import GoogleAPI

LOG = log.get_logger()

DOC_MIME = "application/vnd.google-apps.document"
TXT_MIME = "text/plain"


def read_transcript_text(g: GoogleAPI, file_id: str, mime_type: str) -> str:
    """Read transcript text from a Google Doc or plain .txt file.

    Args:
        g:         Authenticated GoogleAPI instance.
        file_id:   Drive file ID.
        mime_type: MIME type of the file.

    Returns:
        Raw transcript text.

    Raises:
        ValueError: If the MIME type is not supported.
        TypeError:  If the Drive client does not expose a bytes download method.
    """
    if mime_type == DOC_MIME:
        return g.drive.export_google_doc_as_text(file_id)

    if mime_type == TXT_MIME:
        for method_name in (
            "download_bytes",
            "download_file_bytes",
            "download_file_as_bytes",
            "get_file_bytes",
        ):
            if hasattr(g.drive, method_name):
                raw = getattr(g.drive, method_name)(file_id)
                if isinstance(raw, (bytes, bytearray)):
                    return bytes(raw).decode("utf-8", errors="replace")

        # Raw service fallback
        if hasattr(g.drive, "service"):
            try:
                req = g.drive.service.files().get_media(fileId=file_id)
                data = req.execute()
                if isinstance(data, (bytes, bytearray)):
                    return bytes(data).decode("utf-8", errors="replace")
            except Exception:
                pass

        raise TypeError(
            "Drive client does not expose a supported bytes download method for text/plain files"
        )

    raise ValueError(f"Unsupported mime type for transcript: {mime_type!r}")


def archive_file(g: GoogleAPI, file_id: str, processed_folder_id: str, name: str) -> None:
    """Move a processed file to the archive folder.

    Per PIPE-005: raw inputs are archived after processing, never deleted.

    Args:
        g:                    Authenticated GoogleAPI instance.
        file_id:              Drive file ID to move.
        processed_folder_id:  Destination folder ID.
        name:                 File name (for logging only).
    """
    LOG.info(
        log.with_log_prefix(
            log.LOG_SUCCESS,
            f"Archiving processed file: {name} → {processed_folder_id}",
        )
    )
    g.drive.move_file(file_id, new_parent_id=processed_folder_id)


def infer_source_type(filename: str) -> str:
    """Infer the transcript source from the filename.

    Returns a SourceType literal string. Falls back to 'unknown'.
    """
    lower = filename.lower()
    if "plaud" in lower:
        return "plaud"
    if "otter" in lower:
        return "otter"
    if "zoom" in lower:
        return "zoom"
    if "meet" in lower:
        return "google_meet"
    return "unknown"
