"""Pydantic models for transcription-cog."""

from __future__ import annotations

from datetime import datetime
from typing import Any, Literal

from pydantic import BaseModel, Field

SessionType = Literal[
    "private_lesson",
    "group_class",
    "other",
]

SourceType = Literal[
    "plaud",
    "otter",
    "zoom",
    "google_meet",
    "manual",
    "unknown",
]

Visibility = Literal["private", "public"]


class DriveFileRecord(BaseModel):
    """A file row returned from the Google Drive API."""

    id: str = Field(..., description="Google Drive file ID.")
    name: str = Field(..., description="Display name of the file.")
    mime_type: str = Field(alias="mimeType", description="MIME type of the file.")
    model_config = {"populate_by_name": True}


class FilenameMetadata(BaseModel):
    """Metadata parsed from a transcript filename."""

    recording_date: str = Field(..., description="ISO date string YYYY-MM-DD.")
    instructors: list[str] = Field(..., description="Instructor names from filename.")
    students: list[str] = Field(..., description="Student names from filename.")
    organization: str = Field(
        ..., description="Organization name from filename, if any."
    )
    session_type: SessionType = Field(
        ..., description="Lesson type inferred from filename."
    )
    topic: str | None = Field(..., description="Optional topic suffix from filename.")


class TranscriptCreatePayload(BaseModel):
    """Payload for POST /v1/wcs/transcripts."""

    raw_text: str = Field(..., description="Full transcript text.")
    source_type: SourceType = Field(..., description="Origin of the transcript file.")
    source_filename: str = Field(..., description="Original filename in Drive.")
    drive_file_id: str = Field(
        ..., description="Google Drive file ID for deduplication."
    )


class SourceCreatePayload(BaseModel):
    """Payload for POST /v1/wcs/sources.

    Mirrors WcsSourceCreate in api-kaianolevine-com/schemas.py.
    """

    transcript_id: str = Field(
        ..., description="UUID of the wcs_transcripts row this lesson is derived from."
    )
    title: str | None = Field(
        default=None, description="Lesson title (from filename topic or extraction)."
    )
    session_date: str | None = Field(
        default=None, description="ISO date string YYYY-MM-DD."
    )
    session_type: SessionType = Field(
        ..., description="private_lesson | group_class | other."
    )
    instructors_raw: list[str] = Field(
        default_factory=list,
        description="Filename-parsed instructor names; authoritative.",
    )
    students_raw: list[str] = Field(
        default_factory=list, description="Filename-parsed student names."
    )
    organization: str = Field(
        default="", description="Filename-parsed organization, if any."
    )
    visibility: Visibility = Field(default="private", description="private | public.")
    is_default_visible: bool = Field(
        default=False, description="If True, any signed-in user can see this source."
    )

    # Extraction metadata
    extractor_version: str = Field(
        ..., description="semver of the cog producing this extraction."
    )
    extractor_model: str = Field(
        ..., description="LLM model identifier (e.g. claude-sonnet-4-5-20250929)."
    )
    extractor_provider: str = Field(..., description="LLM provider (e.g. anthropic).")
    prompt_version: str = Field(..., description="PROMPT_VERSION from prompt.py.")

    raw_output: dict[str, Any] = Field(
        ..., description="The full extraction shape per EXTRACTION_SCHEMA."
    )


class TranscriptResponse(BaseModel):
    """Response shape for POST /v1/wcs/transcripts."""

    id: str = Field(..., description="UUID of the created transcript row.")
    created_at: datetime = Field(..., description="Timestamp when the row was created.")


class SourceResponse(BaseModel):
    """Response shape for POST /v1/wcs/sources."""

    id: str
    transcript_id: str
    title: str | None = None
    session_date: str | None = None
    session_type: str
    instructors_raw: list[str] = Field(default_factory=list)
    students_raw: list[str] = Field(default_factory=list)
    organization: str = ""
    visibility: str
    is_default_visible: bool
    created_at: datetime
