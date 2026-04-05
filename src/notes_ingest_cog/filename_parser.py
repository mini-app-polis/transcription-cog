"""Parse Drive transcript filenames into structured session metadata.

Convention (examples):
  ``2026-04-01 Kaiano > Sarah - Connection.txt`` — private lesson
  ``2026-04-01 Kaiano > Swingesota.txt`` — group class (organization)

Pattern: ``{YYYY-MM-DD} {instructors} > {tail}``

* ``instructors`` — comma-separated names before ``>``.
* If ``tail`` contains `` - `` (space-hyphen-space), it is a private lesson:
  left side is comma-separated students, right side is the topic.
* Otherwise ``tail`` is the organization name (group class).
"""

from __future__ import annotations

import re
from pathlib import Path

from .models import FilenameMetadata

_FILENAME_RE = re.compile(
    r"^(\d{4}-\d{2}-\d{2})\s+(.+?)\s*>\s*(.+)$",
    re.DOTALL,
)


class FilenameParseError(ValueError):
    """Raised when a filename does not match the expected convention."""


def _split_names(part: str) -> list[str]:
    names = [p.strip() for p in part.split(",")]
    return [n for n in names if n]


def parse_filename(filename: str) -> FilenameMetadata:
    """Parse a Drive file name (with extension) into FilenameMetadata.

    Raises:
        FilenameParseError: If the name cannot be parsed.
    """
    # Preserve trailing whitespace after `` - `` so ``Topic - .ext`` can mean an empty topic;
    # only trim leading whitespace.
    stem = Path(filename).stem.lstrip()
    if not stem:
        raise FilenameParseError("empty filename")

    m = _FILENAME_RE.match(stem)
    if not m:
        raise FilenameParseError(
            f"filename does not match expected pattern: {filename!r}"
        )

    recording_date, instructors_raw, tail = m.groups()
    instructors = _split_names(instructors_raw)
    if not instructors:
        raise FilenameParseError("no instructors before '>'")

    # lstrip only: stripping the tail would turn ``Sarah - `` into ``Sarah -`` and
    # break detection of `` - `` (space-hyphen-space) for an empty topic.
    tail_l = tail.lstrip()
    if not tail_l:
        raise FilenameParseError("nothing after '>'")

    private_sep = " - "
    if private_sep in tail_l:
        left, topic_raw = tail_l.split(private_sep, 1)
        students = _split_names(left)
        if not students:
            raise FilenameParseError(
                "private lesson requires at least one student name"
            )
        topic = topic_raw.strip() or None
        return FilenameMetadata(
            recording_date=recording_date,
            session_type="private_lesson",
            instructors=instructors,
            students=students,
            organization="",
            topic=topic,
        )

    return FilenameMetadata(
        recording_date=recording_date,
        session_type="group_class",
        instructors=instructors,
        students=[],
        organization=tail_l.strip(),
        topic=None,
    )


# Alias for call sites that prefer a distinct type name for parse results.
ParsedFilename = FilenameMetadata
