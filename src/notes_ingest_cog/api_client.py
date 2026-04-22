"""Internal API client for notes-ingest-cog.

Wraps KaianoApiClient from common-python-utils to provide typed methods
for the wcs_transcripts, wcs_notes, and pipeline_evaluations endpoints on
api-kaianolevine-com.

Auth: Clerk M2M JWT via KaianoApiClient (Project Keystone). Machine secret
is read from KAIANO_API_CLERK_MACHINE_SECRET at client construction time;
the shared client handles token acquisition, caching, and Authorization:
Bearer header injection.
"""

from __future__ import annotations

from typing import Any

from mini_app_polis.api import KaianoApiClient

from .models import (
    NoteCreatePayload,
    NoteResponse,
    TranscriptCreatePayload,
    TranscriptResponse,
)


class NotesApiClient:
    """Typed client for the /v1/wcs/* endpoints on api-kaianolevine-com."""

    def __init__(self) -> None:
        # KaianoApiClient.from_env() reads KAIANO_API_BASE_URL and
        # KAIANO_API_CLERK_MACHINE_SECRET. It handles Clerk M2M JWT
        # acquisition, caching, and refresh.
        self._client = KaianoApiClient.from_env()

    def create_transcript(self, payload: TranscriptCreatePayload) -> TranscriptResponse:
        """POST /v1/wcs/transcripts — store raw transcript, return record."""
        response = self._client.post(
            "/v1/wcs/transcripts",
            payload.model_dump(),
        )
        return TranscriptResponse(**response["data"])

    def create_note(self, payload: NoteCreatePayload) -> NoteResponse:
        """POST /v1/wcs/notes — store processed notes, return record."""
        response = self._client.post(
            "/v1/wcs/notes",
            payload.model_dump(),
        )
        return NoteResponse(**response["data"])

    def post_evaluation(
        self,
        *,
        source: str,
        source_ref: str,
        severity: str,
        dimension: str,
        detail: str,
        meta: dict[str, Any],
    ) -> None:
        """POST /v1/evaluations — pipeline quality signal (best-effort at call site)."""
        self._client.post(
            "/v1/evaluations",
            {
                "source": source,
                "source_ref": source_ref,
                "severity": severity,
                "dimension": dimension,
                "detail": detail,
                "meta": meta,
            },
        )
