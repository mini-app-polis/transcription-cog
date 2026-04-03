"""Tests for prompt.py — normalization and output shape."""

from __future__ import annotations

from notes_ingest_cog.prompt import build_messages


def test_build_messages_returns_two_messages() -> None:
    """Output is always a system + user message pair."""
    messages = build_messages("hello world")
    assert len(messages) == 2
    assert messages[0]["role"] == "system"
    assert messages[1]["role"] == "user"


def test_build_messages_transcript_in_user() -> None:
    """Transcript text appears in the user message."""
    transcript = "Instructor said: keep your frame."
    messages = build_messages(transcript)
    assert transcript in messages[1]["content"]


def test_build_messages_filename_in_user() -> None:
    """Source filename appears in the user message when provided."""
    messages = build_messages(
        "some transcript", source_filename="2024-01-15_lesson.txt"
    )
    assert "2024-01-15_lesson.txt" in messages[1]["content"]


def test_build_messages_no_filename_no_prefix() -> None:
    """User message has no filename line when source_filename is empty."""
    messages = build_messages("transcript text", source_filename="")
    assert "Source filename:" not in messages[1]["content"]


def test_build_messages_system_contains_known_sections() -> None:
    """System prompt contains key section names."""
    messages = build_messages("transcript")
    system = messages[0]["content"]
    for section in ("key_concepts", "vocabulary_terms", "drills", "action_items"):
        assert section in system


def test_build_messages_system_contains_session_types() -> None:
    """System prompt documents the new session_type taxonomy."""
    messages = build_messages("transcript")
    system = messages[0]["content"]
    assert "class_taught" in system
    assert "class_attended" in system
