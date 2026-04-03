"""Internal API client for notes-ingest-cog.

Wraps KaianoApiClient from common-python-utils to provide typed methods
for the wcs_transcripts and wcs_notes endpoints on api-kaianolevine-com.

Auth: per-caller X-Internal-API-Key header via get_internal_headers().
"""

from __future__ import annotations

import os

from mini_app_polis.api import KaianoApiClient

from .models import (
    NoteCreatePayload,
    NoteResponse,
    TranscriptCreatePayload,
    TranscriptResponse,
)


def _build_client(base_url: str, internal_key: str) -> KaianoApiClient:
    """Build a KaianoApiClient with internal API key auth."""
    os.environ["KAIANO_API_BASE_URL"] = base_url
    os.environ["KAIANO_API_INTERNAL_KEY"] = internal_key
    return KaianoApiClient.from_env()


class NotesApiClient:
    """Typed client for the /v1/wcs/* endpoints on api-kaianolevine-com."""

    def __init__(self, base_url: str, internal_key: str) -> None:
        self._client = _build_client(base_url, internal_key)
        self._internal_key = internal_key

    def _internal_headers(self) -> dict[str, str]:
        return {
            "Content-Type": "application/json",
            "X-Internal-API-Key": self._internal_key,
        }

    def create_transcript(self, payload: TranscriptCreatePayload) -> TranscriptResponse:
        """POST /v1/wcs/transcripts — store raw transcript, return record ID."""
        response = self._client.post(
            "/v1/wcs/transcripts",
            payload.model_dump(),
        )
        return TranscriptResponse(**response["data"])

    def create_note(self, payload: NoteCreatePayload) -> NoteResponse:
        """POST /v1/wcs/notes — store processed notes, return record ID."""
        response = self._client.post(
            "/v1/wcs/notes",
            payload.model_dump(),
        )
        return NoteResponse(**response["data"])
