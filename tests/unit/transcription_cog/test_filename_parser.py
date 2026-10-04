"""Tests for filename_parser.py."""

from __future__ import annotations

import pytest

from transcription_cog.filename_parser import FilenameParseError, parse_filename


def test_single_instructor_single_student() -> None:
    p = parse_filename("2026-04-01 Kaiano > Sarah.txt")
    assert p.recording_date == "2026-04-01"
    assert p.instructors == ["Kaiano"]
    assert p.students == ["Sarah"]
    assert p.organization == ""
    assert p.session_type == "private_lesson"
    assert p.topic is None


def test_multiple_students() -> None:
    p = parse_filename("2026-04-01 Kaiano > Sarah+Mike.txt")
    assert p.session_type == "private_lesson"
    assert p.students == ["Sarah", "Mike"]
    assert p.organization == ""


def test_multiple_instructors_single_student() -> None:
    p = parse_filename("2026-04-01 Margie+Kaiano > Sarah.txt")
    assert p.instructors == ["Margie", "Kaiano"]
    assert p.students == ["Sarah"]
    assert p.session_type == "private_lesson"


def test_student_is_instructor_reversed_roles() -> None:
    p = parse_filename("2026-04-01 Margie > Kaiano.txt")
    assert p.instructors == ["Margie"]
    assert p.students == ["Kaiano"]
    assert p.session_type == "private_lesson"


def test_private_lesson_with_topic() -> None:
    p = parse_filename("2026-04-01 Kaiano > Sarah - Connection and Frame.txt")
    assert p.topic == "Connection and Frame"
    assert p.session_type == "private_lesson"


def test_known_org_swingesota() -> None:
    p = parse_filename("2026-02-10 Kaiano > Swingesota Westie Academy.txt")
    assert p.session_type == "group_class"
    assert p.organization == "Swingesota Westie Academy"
    assert p.students == []


def test_known_org_freedom_swing() -> None:
    p = parse_filename("2026-03-15 Kaiano > Freedom Swing 2025.txt")
    assert p.session_type == "group_class"
    assert p.organization == "Freedom Swing 2025"


def test_group_class_with_topic() -> None:
    p = parse_filename("2026-02-10 Kaiano > Swingesota - Clacky McSassy.txt")
    assert p.topic == "Clacky McSassy"
    assert p.session_type == "group_class"


def test_multiword_org_treated_as_group() -> None:
    p = parse_filename("2026-04-01 Margie > Swing In The North 2026.txt")
    assert p.session_type == "group_class"


def test_recording_date_extracted() -> None:
    p = parse_filename("2024-12-31 Kaiano > Sarah.txt")
    assert p.recording_date == "2024-12-31"


def test_case_insensitive_names() -> None:
    p = parse_filename("2026-04-01 kaiano > sarah.txt")
    assert p.instructors == ["kaiano"]
    assert p.students == ["sarah"]


def test_google_doc_no_extension() -> None:
    p = parse_filename("2026-04-01 Kaiano > Sarah")
    assert p.recording_date == "2026-04-01"
    assert p.session_type == "private_lesson"


def test_txt_extension_stripped() -> None:
    p = parse_filename("2026-04-01 Kaiano > Swingesota.txt")
    assert p.organization == "Swingesota"


def test_underscore_prefix_fails() -> None:
    with pytest.raises(FilenameParseError, match="date"):
        parse_filename("_2026-04-01 Kaiano > Sarah.txt")


def test_missing_date_fails() -> None:
    with pytest.raises(FilenameParseError):
        parse_filename("Kaiano > Sarah.txt")


def test_missing_arrow_fails() -> None:
    with pytest.raises(FilenameParseError, match=">"):
        parse_filename("2026-04-01 Kaiano Sarah.txt")


def test_empty_left_side_fails() -> None:
    with pytest.raises(FilenameParseError):
        parse_filename("2026-04-01  > Sarah.txt")


def test_empty_right_side_fails() -> None:
    with pytest.raises(FilenameParseError):
        parse_filename("2026-04-01 Kaiano > .txt")


def test_wrong_date_format_fails() -> None:
    with pytest.raises(FilenameParseError):
        parse_filename("04-01-2026 Kaiano > Sarah.txt")


def test_all_people_populated() -> None:
    p = parse_filename("2026-04-01 Margie+Kaiano > Sarah+Mike.txt")
    assert set(p.all_people) == {"Margie", "Kaiano", "Sarah", "Mike"}


def test_raw_filename_preserved() -> None:
    filename = "2026-04-01 Kaiano > Sarah - Topic.txt"
    p = parse_filename(filename)
    assert p.raw_filename == filename
