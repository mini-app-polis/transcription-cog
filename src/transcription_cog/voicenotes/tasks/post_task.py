"""Prefect task: post extracted task to Todoist."""

from __future__ import annotations

from datetime import UTC, date, datetime

from prefect import task

from transcription_cog.voicenotes._shared import get_logger
from transcription_cog.voicenotes.clients.todoist_client import (
    TodoistTaskInput,
    get_todoist_client,
)
from transcription_cog.voicenotes.config import settings
from transcription_cog.voicenotes.models.extracted_task import ExtractedTask

_logger = get_logger("voicenotes-cog")


def _today() -> date:
    """Return today's date in UTC.

    Wrapped in a helper so tests can monkeypatch a fixed date for
    deterministic assertions on ``compose_task_input``. Production
    code should always go through this function rather than calling
    ``datetime.now()`` directly.

    Note on timezone: we use UTC (the deployment runs there) and
    let Todoist render the date in the user's account timezone.
    Edge case: a recording made just before midnight in the user's
    local time may land on the next UTC date and therefore appear
    as "tomorrow" in their Today view. If this becomes a recurring
    issue, add a ``TODOIST_USER_TIMEZONE`` setting and convert here.
    """
    return datetime.now(UTC).date()


_TASK_RETRIES = settings.task_retries
_TASK_RETRY_DELAYS = settings.task_retry_delays_seconds


# Canonical Drive view URL for the source audio. Rendered as a
# clickable markdown link in the task description footer (Todoist
# renders ``[Audio](https://...)`` as a link), and serves double duty
# as the idempotency marker — ``find_task_by_drive_file_id`` scans
# active task descriptions for this URL substring to detect duplicate
# processing on retry. Drive file IDs are stable across the
# inbox→processed move at archive time, so the link continues to work
# until the cleanup flow eventually deletes the audio.
DRIVE_FILE_VIEW_URL_TEMPLATE = "https://drive.google.com/file/d/{drive_file_id}/view"

# Label applied when Claude flagged the note for review.
_REVIEW_LABEL = "review"

# Fallback title when Claude returns an empty/whitespace-only title.
# Todoist's ``content`` (= task title) field has a min-length of 1
# and a 400 with ``error_tag=INVA`` on ``minlen`` is the result if
# we send an empty string. This happens in practice when the
# transcript is gibberish or all-filler — Claude correctly flags
# ``needs_review=true`` but produces an empty title. Substituting a
# fallback lets the task land in Todoist with the ``review`` label
# so the user can triage it, instead of looping retries forever and
# stranding the audio file in ``voice-inbox/``.
_EMPTY_TITLE_FALLBACK = "[Voice note needs review]"

# Human labels for the auxiliary 5W sections rendered below the
# description. Internal field names on ExtractedTask use the 5W
# vocabulary (where/who/when) so the prompt and the model speak the
# same language; the user-facing labels live here so they can change
# without touching the model or prompt.
_METADATA_LABELS: tuple[tuple[str, str], ...] = (
    ("where", "Context"),
    ("who", "Peers"),
    ("when", "Timeline"),
)


def _format_section(label: str, value: str) -> str:
    """Format one labeled section as ``**Label**\\n<value>``.

    Header sits directly above its value (no blank line between)
    so each section reads as one tight unit. Sections are joined
    by ``\\n\\n`` in ``compose_description`` so blank lines appear
    between sections, not within them.
    """
    return f"**{label}**\n{value}"


def _audio_section_value(drive_file_id: str) -> str:
    """Build the value of the Audio section: a markdown link to Drive.

    Renders as ``[Listen](https://drive.google.com/file/d/<id>/view)``.
    Todoist displays "Listen" as a clickable link that opens the
    Drive viewer in the browser, where the user can play the
    original recording.

    The URL is also the idempotency marker — its substring uniquely
    identifies the source file so ``find_task_by_drive_file_id`` can
    detect duplicate processing on retry without a separate
    HTML-comment marker.
    """
    url = DRIVE_FILE_VIEW_URL_TEMPLATE.format(drive_file_id=drive_file_id)
    return f"[Listen]({url})"


def _render_metadata_sections(extracted: ExtractedTask) -> list[str]:
    """Return one formatted section per present auxiliary 5W field.

    Iterates ``_METADATA_LABELS`` in order so field placement is fixed
    even when some Ws are absent — the user can scan to a known
    position without reading labels. Empty/None fields contribute no
    section at all (no blank header, no blank value).
    """
    sections: list[str] = []
    for field_name, label in _METADATA_LABELS:
        value = getattr(extracted, field_name, None)
        if value:
            sections.append(_format_section(label, value))
    return sections


def compose_description(extracted: ExtractedTask, drive_file_id: str) -> str:
    """Build the Todoist task description body.

    Layout — the description prose leads (no header — Todoist's own
    rendering of the task body makes "this is the description"
    obvious), then each auxiliary section gets a bold header, a
    blank line, then its value, with blank lines between sections::

        <description prose>

        **Context**       ← only if where is set
        <where>

        **Peers**         ← only if who is set
        <who>

        **Timeline**      ← only if when is set
        <when>

        **Audio**

        [Listen](https://drive.google.com/file/d/…/view)

    Sparse notes (no auxiliary Ws) render as just description prose
    + Audio section. The Audio section is always present — its URL
    doubles as the dedup marker.
    """
    sections: list[str] = []
    if extracted.description:
        sections.append(extracted.description)
    sections.extend(_render_metadata_sections(extracted))
    sections.append(_format_section("Audio", _audio_section_value(drive_file_id)))
    return "\n\n".join(sections)


def compose_task_input(
    extracted: ExtractedTask, drive_file_id: str
) -> TodoistTaskInput:
    """Map ``ExtractedTask`` + drive_file_id → Todoist payload shape.

    Due date is always forced to today (UTC) regardless of what
    Claude returned. Voice notes captured today land in the user's
    Today view in Todoist by default; if the actual deadline is
    different, the user adjusts it manually after triage. Claude's
    extracted ``due_date`` is kept on ``ExtractedTask`` for
    telemetry only — see ``has_due_date`` in the start log.

    Labels are merged from two sources:

      - ``_REVIEW_LABEL`` — system-controlled, applied when the task
        needs human triage (gibberish, empty title fallback).
      - ``extracted.labels`` — Claude-suggested semantic tags (open
        vocabulary, normalized by the model validator). Skipped when
        the review path fires so review tasks don't accumulate
        ad-hoc labels alongside the system flag.

    Empty/whitespace-only titles are replaced with
    ``_EMPTY_TITLE_FALLBACK`` so Todoist's ``content`` minlen=1
    constraint is always satisfied. Always pairs with the ``review``
    label so the user can find and triage the result.
    """
    title = extracted.title.strip() if extracted.title else ""
    needs_review = extracted.needs_review or not title
    if not title:
        title = _EMPTY_TITLE_FALLBACK
    if needs_review:
        labels: tuple[str, ...] = (_REVIEW_LABEL,)
    else:
        labels = tuple(extracted.labels)
    return TodoistTaskInput(
        title=title,
        description=compose_description(extracted, drive_file_id),
        project_id=settings.todoist_inbox_project_id,
        due_date=_today(),
        labels=labels,
    )


@task(
    name="post_task",
    retries=_TASK_RETRIES,
    retry_delay_seconds=_TASK_RETRY_DELAYS,
)
def post_task(
    extracted: ExtractedTask,
    drive_file_id: str,
) -> str:
    """Create the Todoist task. Returns the task ID.

    Idempotency:
      - Every task description embeds the source audio's Drive view
        URL via the audio footer link.
      - Before posting, scan active tasks in the project for that URL
        substring.
      - If found: log warning, return existing task ID, no duplicate.
    """
    _logger.info(
        "voicenotes.todoist_post.start",
        category="pipeline",
        context={
            "drive_file_id": drive_file_id,
            "title": extracted.title,
            "needs_review": extracted.needs_review,
            "has_due_date": extracted.due_date is not None,
        },
    )

    client = get_todoist_client()
    project_id = settings.todoist_inbox_project_id

    # Idempotency check first. The audio footer URL is the unique
    # marker — it's embedded in every task description we create, and
    # is stable across the inbox→processed file move.
    marker = DRIVE_FILE_VIEW_URL_TEMPLATE.format(drive_file_id=drive_file_id)
    existing_id = client.find_task_by_drive_file_id(project_id, marker)
    if existing_id is not None:
        _logger.warning(
            "voicenotes.todoist_post.duplicate_skipped",
            category="data",
            context={
                "drive_file_id": drive_file_id,
                "existing_task_id": existing_id,
            },
        )
        return existing_id

    payload = compose_task_input(extracted, drive_file_id)
    try:
        task_id = client.create_task(payload)
    except Exception as exc:
        _logger.error(
            "voicenotes.todoist_post.failure",
            category="pipeline",
            context={
                "drive_file_id": drive_file_id,
                "error": str(exc),
            },
        )
        raise

    _logger.info(
        "voicenotes.todoist_post.success",
        category="pipeline",
        context={
            "drive_file_id": drive_file_id,
            "todoist_task_id": task_id,
            "labeled_review": extracted.needs_review,
        },
    )
    return task_id
