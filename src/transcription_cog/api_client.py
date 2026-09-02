"""Internal API client for transcription-cog.

Wraps KaianoApiClient from common-python-utils to provide typed methods
for the wcs_transcripts and wcs_sources endpoints on api-kaianolevine-com.

For posting pipeline evaluations to ``/v1/evaluations``, use the
transcription-cog shim around :mod:`mini_app_polis.pipeline_status`
(see :mod:`transcription_cog._pipeline_eval`). The shim owns the
payload shape and best-effort semantics for evaluation findings; this
client stays focused on the cog's domain endpoints.

Auth: this cog's own named API key, read from TRANSCRIPTION_COG_API_KEY by
the shared client, which derives that name from MACHINE_NAME below. The key
identifies the cog, so the API's audit trail records which cog wrote — not
merely that a cog did.

Falls back to the shared Clerk machine secret when the key is unset, which is
the previous behaviour and the rollback path.
"""

from __future__ import annotations

from mini_app_polis.api import KaianoApiClient

from .models import (
    SourceCreatePayload,
    SourceResponse,
    TranscriptCreatePayload,
    TranscriptResponse,
)

#: This cog's name in api-kaianolevine-com's identity_registry.MACHINES. The
#: shared client derives TRANSCRIPTION_COG_API_KEY from it, and the API
#: derives the same variable from the same name.
MACHINE_NAME = "transcription-cog"


class SubstrateApiClient:
    """Typed client for the /v1/wcs/* substrate endpoints on api-kaianolevine-com."""

    def __init__(self) -> None:
        # Presents this cog's own key (TRANSCRIPTION_COG_API_KEY), falling
        # back to the shared Clerk machine secret when it is unset.
        self._client = KaianoApiClient.from_env(MACHINE_NAME)

    def create_transcript(self, payload: TranscriptCreatePayload) -> TranscriptResponse:
        """POST /v1/wcs/transcripts — store raw transcript, return record."""
        response = self._client.post(
            "/v1/wcs/transcripts",
            payload.model_dump(),
        )
        return TranscriptResponse(**response["data"])

    def create_source(self, payload: SourceCreatePayload) -> SourceResponse:
        """POST /v1/wcs/sources — ingest a source with its extraction.

        Creates a wcs_sources row (or updates an existing one for the same
        transcript_id), writes a new active wcs_source_extractions row, and
        triggers compose_source on the API side. Returns the source record.

        See api-kaianolevine-com/routers/wcs_sources.py for the write
        endpoint's idempotency contract: one source per transcript, multiple
        extractions over time.
        """
        response = self._client.post(
            "/v1/wcs/sources",
            payload.model_dump(),
        )
        return SourceResponse(**response["data"])
