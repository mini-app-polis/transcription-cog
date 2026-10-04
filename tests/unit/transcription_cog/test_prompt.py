"""Tests for prompt.py and EXTRACTION_SCHEMA validation."""

from __future__ import annotations

import pytest
from jsonschema import ValidationError, validate

from transcription_cog.filename_parser import parse_filename
from transcription_cog.prompt import build_messages
from transcription_cog.schema import EXTRACTION_SCHEMA


def _minimal_valid_extraction() -> dict:
    return {
        "title": "Test lesson",
        "summary": "A short summary of the lesson content.",
    }


# ── prompt construction ───────────────────────────────────────────────────────


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
    for section in ("entities", "entity_definitions", "drill_purposes", "action_items"):
        assert section in system


def test_system_instructs_no_guessing() -> None:
    parsed = parse_filename("2026-04-01 Kaiano > Sarah.txt")
    messages = build_messages("transcript", parsed=parsed)
    assert "HIGH CONFIDENCE OR BLANK" in messages[0]["content"]


# ── EXTRACTION_SCHEMA validation ──────────────────────────────────────────────


def test_schema_accepts_long_skill_description_that_previously_failed() -> None:
    """Regression test for the Robert Royston workshop case: a 161-char
    skill description is now valid. Previously failed under the
    _SKILL_DESCRIPTION_MAX_LENGTH=120 cap.
    """
    extraction = _minimal_valid_extraction()
    extraction["drill_purposes"] = [
        {
            "drill_name": "second-step push drill",
            "skill_description": (
                "Control of the first step and half rotation with no "
                "collection; ability to maintain the push through the "
                "second step without collecting."
            ),
            "focus_context": "",
        }
    ]
    validate(instance=extraction, schema=EXTRACTION_SCHEMA)


def test_schema_accepts_long_entity_name() -> None:
    """Entity names are no longer capped at 80 characters."""
    extraction = _minimal_valid_extraction()
    extraction["entities"] = [
        {
            "kind": "drill",
            "name": (
                "alternating collagen recoil and muscular dampening landings "
                "with lateral push direction-finding emphasis"
            ),
            "prose": "",
        }
    ]
    validate(instance=extraction, schema=EXTRACTION_SCHEMA)


def test_schema_rejects_drill_purpose_missing_required_field() -> None:
    extraction = _minimal_valid_extraction()
    extraction["drill_purposes"] = [
        {
            "drill_name": "second-step push drill",
            # skill_description intentionally omitted
        }
    ]
    with pytest.raises(ValidationError):
        validate(instance=extraction, schema=EXTRACTION_SCHEMA)
