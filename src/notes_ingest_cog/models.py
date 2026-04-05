"""Pydantic models for notes-ingest-cog."""

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
    id: str
    name: str
    mime_type: str = Field(alias="mimeType")
    model_config = {"populate_by_name": True}


class FilenameMetadata(BaseModel):
    recording_date: str
    instructors: list[str]
    students: list[str]
    organization: str
    session_type: SessionType
    topic: str | None


class NotesOutput(BaseModel):
    title: str | None = None
    session_type: str | None = None
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


class TranscriptCreatePayload(BaseModel):
    raw_text: str
    source_type: SourceType
    source_filename: str
    drive_file_id: str


class NoteCreatePayload(BaseModel):
    transcript_id: str
    title: str | None
    session_date: str | None
    session_type: SessionType
    instructors: list[str]
    students: list[str]
    organization: str
    visibility: Visibility
    model: str
    provider: str
    notes_json: dict[str, Any]


class TranscriptResponse(BaseModel):
    id: str
    created_at: datetime


class NoteResponse(BaseModel):
    id: str
    created_at: datetime
