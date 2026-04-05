"""Pydantic models for notes-ingest-cog.

All external data (Drive file metadata, LLM output, API payloads) is
validated through these models before use. Never access raw dicts directly.
"""

from __future__ import annotations

from datetime import datetime
from typing import Any, Literal

from pydantic import BaseModel, Field

# ── Session / source taxonomy ─────────────────────────────────────────────────

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


# ── Parsed filename (authoritative session metadata) ──────────────────────────


class FilenameMetadata(BaseModel):
    """Structured fields derived from the transcript filename."""

    recording_date: str
    session_type: SessionType
    instructors: list[str]
    students: list[str]
    organization: str
    topic: str | None = None


# ── Drive file metadata ───────────────────────────────────────────────────────


class DriveFileRecord(BaseModel):
    """Validated metadata for a file returned from the Drive API."""

    id: str
    name: str
    mime_type: str = Field(alias="mimeType")

    model_config = {"populate_by_name": True}


# ── LLM output ───────────────────────────────────────────────────────────────


class NotesOutput(BaseModel):
    """Validated structured notes produced by the LLM.

    Matches the NOTES_SCHEMA in schema.py. All fields are optional
    because the LLM only populates sections present in the transcript.
    The JSONB storage model means schema evolution is additive.
    """

    title: str | None = None
    summary: str | None = None
    key_concepts: list[Any] = Field(default_factory=list)
    vocabulary_terms: list[dict[str, Any]] = Field(default_factory=list)
    drills: list[dict[str, Any]] = Field(default_factory=list)
    common_mistakes: list[dict[str, Any]] = Field(default_factory=list)
    patterns_and_sequences: list[Any] = Field(default_factory=list)
    student_observations: list[Any] = Field(default_factory=list)
    action_items: list[Any] = Field(default_factory=list)
    competition_notes: list[Any] = Field(default_factory=list)
    quotes: list[Any] = Field(default_factory=list)
    references: list[Any] = Field(default_factory=list)
    off_topic_notes: list[Any] = Field(default_factory=list)
    suggested_new_sections: list[dict[str, Any]] = Field(default_factory=list)

    model_config = {"extra": "allow"}


# ── API payloads ──────────────────────────────────────────────────────────────


class TranscriptCreatePayload(BaseModel):
    """POST /v1/wcs/transcripts request body."""

    raw_text: str
    source_type: SourceType
    source_filename: str
    drive_file_id: str


class NoteCreatePayload(BaseModel):
    """POST /v1/wcs/notes request body."""

    transcript_id: str
    title: str | None
    session_date: str | None
    session_type: SessionType
    visibility: Visibility
    model: str
    provider: str
    notes_json: dict[str, Any]
    instructors: list[str]
    students: list[str]
    organization: str


# ── API responses ─────────────────────────────────────────────────────────────


class TranscriptResponse(BaseModel):
    """Response from POST /v1/wcs/transcripts."""

    id: str
    created_at: datetime


class NoteResponse(BaseModel):
    """Response from POST /v1/wcs/notes."""

    id: str
    created_at: datetime
