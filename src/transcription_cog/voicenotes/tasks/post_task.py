"""Prefect task: post the extracted task to Asana."""

from __future__ import annotations

from datetime import UTC, date, datetime

from mini_app_polis.asana import (
    AsanaClient,
    AsanaTaskInput,
    escape_rich_text,
    link,
    rich_text_body,
)
from prefect import task

from transcription_cog.voicenotes._shared import get_logger
from transcription_cog.voicenotes.clients.asana_client import get_asana_client
from transcription_cog.voicenotes.config import settings
from transcription_cog.voicenotes.models.extracted_task import ExtractedTask

_logger = get_logger("voicenotes-cog")


def _today() -> date:
    """Return today's date in UTC.

    Wrapped in a helper so tests can monkeypatch a fixed date for
    deterministic assertions on ``compose_task_input``. Production
    code should always go through this function rather than calling
    ``datetime.now()`` directly.

    Note on timezone: we use UTC (the deployment runs there) and let
    Asana render the date in the account timezone. Edge case: a
    recording made just before midnight in the user's local time may
    land on the next UTC date and therefore appear as "tomorrow" in
    their My Tasks view. If this becomes a recurring issue, add an
    ``ASANA_USER_TIMEZONE`` setting and convert here.
    """
    return datetime.now(UTC).date()


_TASK_RETRIES = settings.task_retries
_TASK_RETRY_DELAYS = settings.task_retry_delays_seconds


# Canonical Drive view URL for the source audio, rendered as a clickable
# link in the task body so the operator can replay the recording. Drive
# file IDs are stable across the inbox -> processed move at archive
# time, so the link keeps working until the cleanup flow deletes the
# audio.
#
# Unlike the Todoist implementation this replaces, the URL is NOT the
# idempotency marker. Todoist had no way to search task bodies, so the
# only available check was to list the project and substring-scan every
# active task's description -- which silently missed any note the
# operator had already completed, since Todoist's list endpoint returns
# active tasks only. Asana addresses a task by its app-scoped
# ``external`` id directly, so dedup is one request and sees completed
# tasks too. The link is now just a link.
DRIVE_FILE_VIEW_URL_TEMPLATE = "https://drive.google.com/file/d/{drive_file_id}/view"

# Namespace for the idempotency key stored on the task's ``external``
# field. The prefix keeps voice notes distinct from other sources that
# will create tasks on the same board (Discord messages, conformance
# findings) without those sources needing to coordinate id formats.
# A period rather than a colon: the id is URL-encoded into
# ``/tasks/external:<id>``, and a colon there reads badly even encoded.
_EXTERNAL_ID_PREFIX = "voicenote."

# Tag applied when Claude flagged the note for review.
_REVIEW_TAG = "review"

# Every voice note is the operator's own, so tasks are assigned to the
# token holder rather than left unassigned -- that puts them in My
# Tasks, not only on the board.
_ASSIGNEE = "me"

# Fallback title when Claude returns an empty/whitespace-only title.
# Asana rejects an empty ``name``. This happens in practice when the
# transcript is gibberish or all-filler -- Claude correctly flags
# ``needs_review=true`` but produces an empty title. Substituting a
# fallback lets the task land with the ``review`` tag so the operator
# can triage it, instead of looping retries forever and stranding the
# audio file in ``voice-inbox/``. The wording carries the review signal
# on its own, which matters because tag resolution is best-effort.
_EMPTY_TITLE_FALLBACK = "[Voice note needs review]"

# The board's card template, mirrored from the "TEMPLATE — do not work"
# card in the intake column. Every card on the board carries these six
# lines, so machine-created ones do too: the point of the template is
# that a groomer fills the blanks in place, and a card missing the
# fields can't be groomed without first being reshaped by hand.
#
# Each entry is ``(label, ExtractedTask field or None)``. Only ``Where``
# has anything a voice note can supply — it already means the system,
# repo or location the work happens in, which is what the board's
# "Where: deejaytools" entries record. The rest are grooming decisions
# (a definition of done, an effort estimate, a PR link) that a voice
# note has no basis to invent, so they are emitted blank rather than
# guessed at.
_TEMPLATE_FIELDS: tuple[tuple[str, str | None], ...] = (
    ("Done when", None),
    ("Where", "where"),
    ("Constraints", None),
    ("Effort", None),
    ("PR", None),
    ("Blocked by", None),
)

# Human labels for the auxiliary sections rendered below the template
# block. Internal field names on ExtractedTask use the 5W vocabulary
# (where/who/when) so the prompt and the model speak the same language;
# the user-facing labels live here so they can change without touching
# the model or prompt. ``where`` is absent because it is carried by the
# template's ``Where`` field above — repeating it below would be the
# same fact in two places.
_METADATA_LABELS: tuple[tuple[str, str], ...] = (
    ("who", "Peers"),
    ("when", "Timeline"),
)


def external_id_for(drive_file_id: str) -> str:
    """Return the Asana ``external`` id for a source audio file."""
    return f"{_EXTERNAL_ID_PREFIX}{drive_file_id}"


def _format_section(label: str, value_html: str) -> str:
    """Format one labeled section as ``<strong>Label</strong>\\n<value>``.

    Header sits directly above its value (no blank line between) so
    each section reads as one tight unit. Sections are joined by
    ``\\n\\n`` in ``rich_text_body`` so blank lines appear between
    sections, not within them.

    ``value_html`` is expected to be already escaped or already markup
    -- this function does not escape, because callers pass a mix of
    both.
    """
    return f"<strong>{label}</strong>\n{value_html}"


def _audio_section_value(drive_file_id: str) -> str:
    """Build the value of the Audio section: a link to the Drive viewer.

    Renders as ``Listen``, opening the original recording in the
    browser.
    """
    url = DRIVE_FILE_VIEW_URL_TEMPLATE.format(drive_file_id=drive_file_id)
    return link(url, "Listen")


def render_template_block(extracted: ExtractedTask) -> str:
    """Render the board's six-field header block.

    All six lines are always emitted, in TEMPLATE's order, whether or
    not the note filled them — an empty ``Effort:`` is a prompt to fill
    it, while an absent one is a card that doesn't match the board.
    Values are escaped; the labels are plain text, not bold, because
    that is how the TEMPLATE card renders them.

    The block is one section: its lines are joined with single newlines
    so they read as a tight header, and ``rich_text_body`` puts the
    blank line between it and the prose below.
    """
    lines: list[str] = []
    for label, field_name in _TEMPLATE_FIELDS:
        value = getattr(extracted, field_name, None) if field_name else None
        if value:
            lines.append(f"{label}: {escape_rich_text(value)}")
        else:
            lines.append(f"{label}:")
    return "\n".join(lines)


def _render_metadata_sections(extracted: ExtractedTask) -> list[str]:
    """Return one formatted section per present auxiliary 5W field.

    Iterates ``_METADATA_LABELS`` in order so field placement is fixed
    even when some Ws are absent -- the operator can scan to a known
    position without reading labels. Empty/None fields contribute no
    section at all (no blank header, no blank value).
    """
    sections: list[str] = []
    for field_name, label in _METADATA_LABELS:
        value = getattr(extracted, field_name, None)
        if value:
            sections.append(_format_section(label, escape_rich_text(value)))
    return sections


def compose_html_notes(extracted: ExtractedTask, drive_file_id: str) -> str:
    """Build the Asana task body as rich text.

    Two regions, which is what the board's TEMPLATE card reserves: the
    six-field header block, then everything the note actually captured
    below the blank line::

        <body>Done when:
        Where: <where>                 <- blank if the note had none
        Constraints:
        Effort:
        PR:
        Blocked by:

        <description prose>

        <strong>Peers</strong>         <- only if who is set
        <who>

        <strong>Timeline</strong>      <- only if when is set
        <when>

        <strong>Audio</strong>
        <a href="https://drive.google.com/file/d/.../view">Listen</a></body>

    The header block is the board's convention and is always complete;
    the region below it is the voicenotes layout carried over from
    Todoist, where a section appears only when the note supplied it.
    A sparse note therefore renders as the header block, the prose and
    the Audio section.

    Everything Claude or Whisper produced is escaped on the way in.
    Asana's ``html_notes`` is a restricted HTML subset rather than the
    markdown Todoist accepted, and it answers 400 on malformed markup
    -- a transcript containing "value < threshold" would otherwise fail
    the whole post.
    """
    sections: list[str] = [render_template_block(extracted)]
    if extracted.description:
        sections.append(escape_rich_text(extracted.description))
    sections.extend(_render_metadata_sections(extracted))
    sections.append(_format_section("Audio", _audio_section_value(drive_file_id)))
    return rich_text_body(*sections)


def resolve_tag_gids(client: AsanaClient, names: tuple[str, ...]) -> tuple[str, ...]:
    """Resolve tag names to gids, skipping any that fail.

    Asana tags are workspace objects with gids, not the free strings
    Todoist labels were, so each name costs a lookup (cached on the
    client for the life of the process) and possibly a create.

    Resolution is best-effort by design. A tag is decoration on a task
    whose content is already correct; failing the post over one would
    strand the note and its audio in ``voice-inbox/`` across every
    retry. Failures are logged at WARNING with the name that failed, so
    a persistently broken tag is visible rather than merely absent --
    the review signal also survives in the title fallback when Claude
    flagged the note.
    """
    gids: list[str] = []
    for name in names:
        try:
            gids.append(client.find_or_create_tag(name))
        except Exception as exc:
            _logger.warning(
                "voicenotes.asana_post.tag_unresolved",
                category="api",
                context={"tag": name, "error": str(exc)},
            )
    return tuple(gids)


def compose_task_input(
    extracted: ExtractedTask,
    drive_file_id: str,
    tag_gids: tuple[str, ...] = (),
) -> AsanaTaskInput:
    """Map ``ExtractedTask`` + drive_file_id -> Asana payload shape.

    Due date is always forced to today (UTC) regardless of what Claude
    returned. Voice notes captured today land in the operator's My
    Tasks for today; if the actual deadline is different, they adjust
    it after triage. Claude's extracted ``due_date`` is kept on
    ``ExtractedTask`` for telemetry only -- see ``has_due_date`` in the
    start log.

    Empty/whitespace-only titles are replaced with
    ``_EMPTY_TITLE_FALLBACK`` so Asana's non-empty ``name`` constraint
    is always satisfied.

    Tag gids are resolved by the caller (``post_task``) rather than
    here, so this function stays pure and testable without an API
    client.
    """
    title = extracted.title.strip() if extracted.title else ""
    if not title:
        title = _EMPTY_TITLE_FALLBACK
    return AsanaTaskInput(
        name=title,
        html_notes=compose_html_notes(extracted, drive_file_id),
        project_gid=settings.asana_inbox_project_id,
        section_gid=settings.asana_inbox_section_id or None,
        assignee=_ASSIGNEE,
        due_on=_today(),
        tag_gids=tag_gids,
        external_id=external_id_for(drive_file_id),
    )


def tag_names_for(extracted: ExtractedTask) -> tuple[str, ...]:
    """Decide which tag names apply to a note.

    Two sources:

      - ``_REVIEW_TAG`` -- system-controlled, applied when the task
        needs human triage (gibberish, or an empty title that forced
        the fallback).
      - ``extracted.labels`` -- Claude-suggested semantic tags (open
        vocabulary, normalized by the model validator). Skipped when
        the review path fires so review tasks do not accumulate ad-hoc
        tags alongside the system flag.
    """
    needs_review = extracted.needs_review or not (extracted.title or "").strip()
    if needs_review:
        return (_REVIEW_TAG,)
    return tuple(extracted.labels)


@task(
    name="post_task",
    retries=_TASK_RETRIES,
    retry_delay_seconds=_TASK_RETRY_DELAYS,
)
def post_task(
    extracted: ExtractedTask,
    drive_file_id: str,
) -> str:
    """Create the Asana task. Returns the task gid.

    Idempotency:
      - Every task carries ``external.gid = voicenote.<drive_file_id>``.
      - Before posting, look that id up directly.
      - If found: log a warning, return the existing gid, no duplicate.

    The lookup finds completed tasks as well as open ones, so a note
    the operator has already triaged and checked off is not recreated
    when a downstream failure replays the file.
    """
    _logger.info(
        "voicenotes.asana_post.start",
        category="pipeline",
        context={
            "drive_file_id": drive_file_id,
            "title": extracted.title,
            "needs_review": extracted.needs_review,
            "has_due_date": extracted.due_date is not None,
        },
    )

    client = get_asana_client()
    external_id = external_id_for(drive_file_id)

    existing_id = client.find_task_by_external_id(external_id)
    if existing_id is not None:
        _logger.warning(
            "voicenotes.asana_post.duplicate_skipped",
            category="data",
            context={
                "drive_file_id": drive_file_id,
                "existing_task_id": existing_id,
            },
        )
        return existing_id

    tag_gids = resolve_tag_gids(client, tag_names_for(extracted))
    payload = compose_task_input(extracted, drive_file_id, tag_gids=tag_gids)
    try:
        task_id = client.create_task(payload)
    except Exception as exc:
        _logger.error(
            "voicenotes.asana_post.failure",
            category="pipeline",
            context={
                "drive_file_id": drive_file_id,
                "error": str(exc),
            },
        )
        raise

    _logger.info(
        "voicenotes.asana_post.success",
        category="pipeline",
        context={
            "drive_file_id": drive_file_id,
            "asana_task_id": task_id,
            "tagged_review": _REVIEW_TAG in tag_names_for(extracted),
        },
    )
    return task_id
