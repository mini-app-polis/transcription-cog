"""Filename convention parser for notes-ingest-cog.

Convention:
    YYYY-MM-DD [instructors] > [students or organization] - [Topic].ext
    YYYY-MM-DD [instructors] > [students or organization].ext

Rules:
    - Date is always first in YYYY-MM-DD format
    - Instructors and students/org are separated by ' > '
    - Multiple instructors or students are separated by '+'
    - Topic after ' - ' is optional
    - Right side determines session type:
        - One or more person names → private_lesson
        - Organization/event name → group_class
    - Case insensitive parsing throughout
    - Files that do not match are rejected with a clear reason
    - Prefix with _ to prevent ingestion (will not match date pattern)

Examples:
    2026-02-10 Kaiano > Swingesota Westie Academy - Clacky McSassy.txt
    2026-04-01 Kaiano > Sarah.txt
    2026-04-01 Kaiano > Sarah+Mike - Connection and Frame.txt
    2026-04-01 Margie+Kaiano > Sarah.txt
    2026-04-01 Margie > Kaiano.txt
    _2026-04-01 Kaiano > Sarah.txt  ← skipped (underscore prefix)
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field

_KNOWN_ORGS: list[str] = [
    "swingesota",
    "swing in the north",
    "westie academy",
    "mnwcsdc",
    "tcrebels",
    "tc rebels",
    "sitn",
]

_DATE_RE = re.compile(r"^(\d{4}-\d{2}-\d{2})\s+(.+)$")
_ARROW_RE = re.compile(r"^(.+?)\s*>\s*(.+)$")


@dataclass(frozen=True)
class ParsedFilename:
    """TODO: describe this class."""

    recording_date: str
    instructors: list[str]
    students: list[str]
    organization: str
    session_type: str
    topic: str | None
    raw_filename: str
    all_people: list[str] = field(default_factory=list)


class FilenameParseError(ValueError):
    """TODO: describe this class."""

    pass


def _strip_extension(filename: str) -> str:
    dot = filename.rfind(".")
    if dot > 0:
        return filename[:dot]
    return filename


def _split_names(raw: str) -> list[str]:
    return [n.strip() for n in raw.split("+") if n.strip()]


def _is_organization(value: str) -> bool:
    lower = value.lower()
    for org in _KNOWN_ORGS:
        if org in lower:
            return True
    if len(value.split()) > 2:
        return True
    return False


def parse_filename(filename: str) -> ParsedFilename:
    """Parse a transcript filename and return structured metadata.

    Raises:
        FilenameParseError: If the filename does not match the convention.
    """
    stem = _strip_extension(filename)

    date_match = _DATE_RE.match(stem)
    if not date_match:
        raise FilenameParseError(
            f"Filename does not start with a date in YYYY-MM-DD format: {filename!r}. "
            "Expected format: 'YYYY-MM-DD Instructor > Student/Org - Topic.ext'"
        )

    recording_date = date_match.group(1)
    remainder = date_match.group(2).strip()

    topic: str | None = None
    if " - " in remainder:
        parts = remainder.split(" - ", 1)
        remainder = parts[0].strip()
        topic = parts[1].strip() or None

    arrow_match = _ARROW_RE.match(remainder)
    if not arrow_match:
        raise FilenameParseError(
            f"Filename missing ' > ' separator between instructors and students/org: {filename!r}. "
            "Expected format: 'YYYY-MM-DD Instructor > Student/Org.ext'"
        )

    left = arrow_match.group(1).strip()
    right = arrow_match.group(2).strip()

    if not left:
        raise FilenameParseError(
            f"No instructor found before ' > ' in filename: {filename!r}"
        )
    if not right:
        raise FilenameParseError(
            f"No student or organization found after ' > ' in filename: {filename!r}"
        )

    instructors = _split_names(left)
    if not instructors:
        raise FilenameParseError(
            f"Could not parse instructor names from {left!r} in filename: {filename!r}"
        )

    right_parts = _split_names(right)

    if len(right_parts) == 1 and _is_organization(right_parts[0]):
        session_type = "group_class"
        students: list[str] = []
        organization = right_parts[0].strip()
    elif len(right_parts) > 1:
        combined = " ".join(right_parts)
        if _is_organization(combined):
            session_type = "group_class"
            students = []
            organization = combined
        else:
            session_type = "private_lesson"
            students = right_parts
            organization = ""
    else:
        session_type = "private_lesson"
        students = right_parts
        organization = ""

    all_people = list(dict.fromkeys(instructors + students))

    return ParsedFilename(
        recording_date=recording_date,
        instructors=instructors,
        students=students,
        organization=organization,
        session_type=session_type,
        topic=topic,
        raw_filename=filename,
        all_people=all_people,
    )
