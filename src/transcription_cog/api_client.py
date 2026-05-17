"""Internal API client for transcription-cog.

Wraps KaianoApiClient from common-python-utils to provide typed methods
for the wcs_transcripts and wcs_notes endpoints on api-kaianolevine-com.

For posting pipeline evaluations to ``/v1/evaluations``, use the
transcription-cog shim around :mod:`mini_app_polis.pipeline_status`
(see :mod:`transcription_cog._pipeline_eval`). The shim owns the
payload shape and best-effort semantics for evaluation findings; this
client stays focused on the cog's domain endpoints.

Auth: Clerk M2M JWT via KaianoApiClient (Project Keystone). Machine secret
is read from KAIANO_API_CLERK_MACHINE_SECRET at client construction time;
the shared client handles token acquisition, caching, and Authorization:
Bearer header injection.
"""

from __future__ import annotations

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
