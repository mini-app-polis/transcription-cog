"""Tests for prompt.py."""

from __future__ import annotations

from transcription_cog.filename_parser import parse_filename
from transcription_cog.prompt import build_messages


def test_build_messages_returns_two_messages() -> None:
    parsed = parse_filename("2026-04-01 Kaiano > Sarah.txt")
    messages = build_messages("transcript text", parsed=parsed)
    assert len(messages) == 2
    assert messages[0]["role"] == "system"
    assert messages[1]["role"] == "user"


def test_transcript_in_user_message() -> None:
    parsed = parse_filename("2026-04-01 Kaiano > Sarah.txt")
    messages = build_messages("keep your frame", parsed=parsed)
    assert "keep your frame" in messages[1]["content"]


def test_filename_in_user_message() -> None:
    filename = "2026-04-01 Kaiano > Sarah - Connection.txt"
    parsed = parse_filename(filename)
    messages = build_messages("transcript", parsed=parsed)
    assert filename in messages[1]["content"]


def test_system_contains_recording_date() -> None:
    parsed = parse_filename("2026-04-01 Kaiano > Sarah.txt")
    messages = build_messages("transcript", parsed=parsed)
    assert "2026-04-01" in messages[0]["content"]


def test_system_contains_instructors() -> None:
    parsed = parse_filename("2026-04-01 Margie+Kaiano > Sarah.txt")
    messages = build_messages("transcript", parsed=parsed)
    system = messages[0]["content"]
    assert "Margie" in system
    assert "Kaiano" in system


def test_system_contains_students_for_private() -> None:
    parsed = parse_filename("2026-04-01 Kaiano > Sarah+Mike.txt")
    messages = build_messages("transcript", parsed=parsed)
    system = messages[0]["content"]
    assert "Sarah" in system
    assert "Mike" in system
    assert "Students" in system


def test_system_contains_organization_for_group() -> None:
    parsed = parse_filename("2026-04-01 Kaiano > Swingesota.txt")
    messages = build_messages("transcript", parsed=parsed)
    system = messages[0]["content"]
    assert "Swingesota" in system
    assert "Organization" in system


def test_system_contains_session_type() -> None:
    parsed = parse_filename("2026-04-01 Kaiano > Sarah.txt")
    messages = build_messages("transcript", parsed=parsed)
    assert "private_lesson" in messages[0]["content"]


def test_system_contains_known_sections() -> None:
    parsed = parse_filename("2026-04-01 Kaiano > Sarah.txt")
    messages = build_messages("transcript", parsed=parsed)
    system = messages[0]["content"]
    for section in ("key_concepts", "vocabulary_terms", "drills", "action_items"):
        assert section in system


def test_system_instructs_no_guessing() -> None:
    parsed = parse_filename("2026-04-01 Kaiano > Sarah.txt")
    messages = build_messages("transcript", parsed=parsed)
    assert "HIGH CONFIDENCE OR BLANK" in messages[0]["content"]
