"""Prefect flows for voicenotes-cog."""

from transcription_cog.voicenotes.flows.cleanup import voicenotes_cleanup
from transcription_cog.voicenotes.flows.ingest import voicenotes_ingest
from transcription_cog.voicenotes.flows.router import voicenotes_router

__all__ = ["voicenotes_cleanup", "voicenotes_ingest", "voicenotes_router"]
