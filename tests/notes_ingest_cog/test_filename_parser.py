"""Tests for filename_parser.parse_filename."""

from __future__ import annotations

import pytest

from notes_ingest_cog.filename_parser import FilenameParseError, parse_filename


def test_parse_private_lesson_with_topic() -> None:
    p = parse_filename("2026-04-01 Kaiano > Sarah - Connection.txt")
    assert p.recording_date == "2026-04-01"
    assert p.session_type == "private_lesson"
    assert p.instructors == ["Kaiano"]
    assert p.students == ["Sarah"]
    assert p.organization == ""
    assert p.topic == "Connection"


def test_parse_group_class_organization() -> None:
    p = parse_filename("2026-04-01 Kaiano > Swingesota.txt")
    assert p.recording_date == "2026-04-01"
    assert p.session_type == "group_class"
    assert p.instructors == ["Kaiano"]
    assert p.students == []
    assert p.organization == "Swingesota"
    assert p.topic is None


def test_parse_multiple_instructors() -> None:
    p = parse_filename("2026-04-01 Kaiano, Mary > Alex - Footwork.gdoc")
    assert p.instructors == ["Kaiano", "Mary"]
    assert p.students == ["Alex"]
    assert p.topic == "Footwork"


def test_parse_multiple_students() -> None:
    p = parse_filename("2026-04-01 Kaiano > Sarah, Alex - Social.txt")
    assert p.students == ["Sarah", "Alex"]
    assert p.topic == "Social"


def test_parse_private_topic_empty_becomes_none() -> None:
    # ``.txt`` would make the stem end with `` - .`` (no trailing space); use ``.md``
    # so the stem ends with `` - `` and the topic segment is empty.
    p = parse_filename("2026-04-01 Kaiano > Sarah - .md")
    assert p.students == ["Sarah"]
    assert p.topic is None


def test_parse_rejects_missing_date() -> None:
    with pytest.raises(FilenameParseError):
        parse_filename("Kaiano > Sarah - Connection.txt")


def test_parse_rejects_missing_arrow() -> None:
    with pytest.raises(FilenameParseError):
        parse_filename("2026-04-01 lesson.txt")


def test_parse_rejects_empty_after_arrow() -> None:
    with pytest.raises(FilenameParseError):
        parse_filename("2026-04-01 Kaiano > .txt")


def test_parse_rejects_no_instructors() -> None:
    with pytest.raises(FilenameParseError):
        parse_filename("2026-04-01  > Sarah - Connection.txt")


def test_parse_rejects_private_without_student() -> None:
    with pytest.raises(FilenameParseError):
        parse_filename("2026-04-01 Kaiano > , - OnlyTopic.txt")
