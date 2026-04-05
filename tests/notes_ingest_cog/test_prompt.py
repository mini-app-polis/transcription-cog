"""Tests for prompt.py — normalization and output shape."""

from __future__ import annotations

from notes_ingest_cog.filename_parser import parse_filename
from notes_ingest_cog.prompt import build_messages


def _sample_private() -> str:
    return "2026-04-01 Kaiano > Sarah - Connection.txt"


def _sample_group() -> str:
    return "2026-04-01 Kaiano > Swingesota.txt"


def test_build_messages_returns_two_messages() -> None:
    """Output is always a system + user message pair."""
    parsed = parse_filename(_sample_private())
    messages = build_messages("hello world", parsed=parsed)
    assert len(messages) == 2
    assert messages[0]["role"] == "system"
    assert messages[1]["role"] == "user"


def test_build_messages_transcript_in_user() -> None:
    """Transcript text appears in the user message."""
    transcript = "Instructor said: keep your frame."
    parsed = parse_filename(_sample_private())
    messages = build_messages(transcript, parsed=parsed)
    assert transcript in messages[1]["content"]


def test_build_messages_user_is_transcript_only() -> None:
    """User message is transcript body only (metadata lives in system)."""
    parsed = parse_filename(_sample_private())
    messages = build_messages("some transcript", parsed=parsed)
    user = messages[1]["content"]
    assert user.startswith("Transcript begins below:")
    assert "some transcript" in user
    assert "Source filename:" not in user


def test_build_messages_system_contains_known_sections() -> None:
    """System prompt contains key section names."""
    parsed = parse_filename(_sample_private())
    messages = build_messages("transcript", parsed=parsed)
    system = messages[0]["content"]
    for section in ("key_concepts", "vocabulary_terms", "drills", "action_items"):
        assert section in system


def test_build_messages_system_injects_filename_metadata_private() -> None:
    """System prompt carries authoritative private-lesson metadata."""
    parsed = parse_filename(_sample_private())
    messages = build_messages("transcript", parsed=parsed)
    system = messages[0]["content"]
    assert "2026-04-01" in system
    assert "private_lesson" in system
    assert "Kaiano" in system
    assert "Sarah" in system
    assert "Connection" in system
    assert "AUTHORITATIVE METADATA" in system
    assert "HIGH CONFIDENCE OR BLANK" in system


def test_build_messages_system_injects_filename_metadata_group() -> None:
    """System prompt carries authoritative group-class metadata."""
    parsed = parse_filename(_sample_group())
    messages = build_messages("transcript", parsed=parsed)
    system = messages[0]["content"]
    assert "group_class" in system
    assert "Swingesota" in system
    assert "HIGH CONFIDENCE OR BLANK" in system
