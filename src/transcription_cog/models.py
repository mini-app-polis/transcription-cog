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
    """TODO: describe this class."""

    id: str = Field(..., description="TODO: describe this field.")
    name: str = Field(..., description="TODO: describe this field.")
    mime_type: str = Field(alias="mimeType", description="TODO: describe this field.")
    model_config = {"populate_by_name": True}


class FilenameMetadata(BaseModel):
    """TODO: describe this class."""

    recording_date: str = Field(..., description="TODO: describe this field.")
    instructors: list[str] = Field(..., description="TODO: describe this field.")
    students: list[str] = Field(..., description="TODO: describe this field.")
    organization: str = Field(..., description="TODO: describe this field.")
    session_type: SessionType = Field(..., description="TODO: describe this field.")
    topic: str | None = Field(..., description="TODO: describe this field.")


class NotesOutput(BaseModel):
    """TODO: describe this class."""

    title: str | None = Field(default=None, description="TODO: describe this field.")
    session_type: str | None = Field(
        default=None, description="TODO: describe this field."
    )
    summary: str | None = Field(default=None, description="TODO: describe this field.")
    key_concepts: list[Any] = Field(
        default_factory=list, description="TODO: describe this field."
    )
    vocabulary_terms: list[dict[str, Any]] = Field(
        default_factory=list, description="TODO: describe this field."
    )
    drills: list[dict[str, Any]] = Field(
        default_factory=list, description="TODO: describe this field."
    )
    common_mistakes: list[dict[str, Any]] = Field(
        default_factory=list, description="TODO: describe this field."
    )
    patterns_and_sequences: list[Any] = Field(
        default_factory=list, description="TODO: describe this field."
    )
    student_observations: list[Any] = Field(
        default_factory=list, description="TODO: describe this field."
    )
    action_items: list[Any] = Field(
        default_factory=list, description="TODO: describe this field."
    )
    competition_notes: list[Any] = Field(
        default_factory=list, description="TODO: describe this field."
    )
    quotes: list[Any] = Field(
        default_factory=list, description="TODO: describe this field."
    )
    references: list[Any] = Field(
        default_factory=list, description="TODO: describe this field."
    )
    off_topic_notes: list[Any] = Field(
        default_factory=list, description="TODO: describe this field."
    )
    suggested_new_sections: list[dict[str, Any]] = Field(
        default_factory=list, description="TODO: describe this field."
    )
    model_config = {"extra": "allow"}


class TranscriptCreatePayload(BaseModel):
    """TODO: describe this class."""

    raw_text: str = Field(..., description="TODO: describe this field.")
    source_type: SourceType = Field(..., description="TODO: describe this field.")
    source_filename: str = Field(..., description="TODO: describe this field.")
    drive_file_id: str = Field(..., description="TODO: describe this field.")


class NoteCreatePayload(BaseModel):
    """TODO: describe this class."""

    transcript_id: str = Field(..., description="TODO: describe this field.")
    title: str | None = Field(..., description="TODO: describe this field.")
    session_date: str | None = Field(..., description="TODO: describe this field.")
    session_type: SessionType = Field(..., description="TODO: describe this field.")
    instructors: list[str] = Field(..., description="TODO: describe this field.")
    students: list[str] = Field(..., description="TODO: describe this field.")
    organization: str = Field(..., description="TODO: describe this field.")
    visibility: Visibility = Field(..., description="TODO: describe this field.")
    model: str = Field(..., description="TODO: describe this field.")
    provider: str = Field(..., description="TODO: describe this field.")
    notes_json: dict[str, Any] = Field(..., description="TODO: describe this field.")


class TranscriptResponse(BaseModel):
    """TODO: describe this class."""

    id: str = Field(..., description="TODO: describe this field.")
    created_at: datetime = Field(..., description="TODO: describe this field.")


class NoteResponse(BaseModel):
    """TODO: describe this class."""

    id: str = Field(..., description="TODO: describe this field.")
    created_at: datetime = Field(..., description="TODO: describe this field.")
