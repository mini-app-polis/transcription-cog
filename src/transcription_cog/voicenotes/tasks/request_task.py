"""Hand the extracted note to the task manager, which puts it on the board.

This cog does the part only it can do: transcribe the audio and extract
what the note asks for. Everything the board needs beyond that (its
template block, the task's id, tags, column and assignee) is the task
manager's, so the note is sent as a draft through ``POST /v1/tasks`` and
this cog never talks to the board itself.

The request is accepted, not completed: the task is created afterwards, by
the task manager, whose run report names it. What this step can say is that
the request landed.

**Asking twice is safe.** The request carries
``voicenote.<drive_file_id>`` as its external id, and the task manager
looks that up before creating anything. A job redelivered after this step
succeeded (say the archive failed) asks again and makes no second task.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, date, datetime
from typing import Final, Literal

from mini_app_polis.api import KaianoApiClient
from mini_app_polis.api.contract import (
    TaskCreateRequest,
    TaskDraft,
    TaskLink,
    TaskSection,
)

from transcription_cog.voicenotes._shared import get_logger
from transcription_cog.voicenotes.models.extracted_task import ExtractedTask

_logger = get_logger("voicenotes-cog")

#: This cog's machine identity; its key is the one the request presents.
MACHINE_NAME = "transcription-cog"

#: The source the task manager knows voice notes by.
SOURCE: Final[Literal["voicenote"]] = "voicenote"

# Canonical Drive view URL for the source audio, carried as a link so the
# recording can be replayed from the task. Drive file ids are stable across
# the inbox -> processed move at archive time, so the link keeps working
# until the retention sweep deletes the audio.
DRIVE_FILE_VIEW_URL_TEMPLATE = "https://drive.google.com/file/d/{drive_file_id}/view"

# The contract's limits. Model output has no length of its own, and a note
# refused for one long field would be lost to a validation error, so the
# draft is cut to fit instead.
_TITLE_MAX = 500
_TEXT_MAX = 20_000
_LABEL_MAX = 50

# Human labels for the extracted fields that become sections. The model's
# own names (who/when) stay internal; these are what the task shows.
_SECTION_LABELS: tuple[tuple[str, str], ...] = (
    ("who", "Peers"),
    ("when", "Timeline"),
)


@dataclass(frozen=True)
class RequestedTask:
    """The request that landed: the queue message it became."""

    message_id: str


def _today() -> date:
    """Today in UTC: every voice note is due the day it is recorded.

    A helper so tests can fix the date. Asana renders it in the account's
    timezone, so a note recorded just before local midnight can show as
    tomorrow.
    """
    return datetime.now(UTC).date()


def external_id_for(drive_file_id: str) -> str:
    """The note's key on the board: its source, then its audio file."""
    return f"{SOURCE}.{drive_file_id}"


def audio_url(drive_file_id: str) -> str:
    """Where the recording can be replayed."""
    return DRIVE_FILE_VIEW_URL_TEMPLATE.format(drive_file_id=drive_file_id)


def _cut(text: str, limit: int) -> str:
    return text if len(text) <= limit else text[: limit - 1].rstrip() + "…"


def draft_for(extracted: ExtractedTask, drive_file_id: str) -> TaskDraft:
    """The note as a task draft.

    A blank title is sent blank: the task manager names it for review rather
    than refusing it. Due today, always; the extracted due date is kept on
    ``ExtractedTask`` for telemetry only.
    """
    sections = [
        TaskSection(label=label, text=_cut(value, _TEXT_MAX))
        for field_name, label in _SECTION_LABELS
        if (value := getattr(extracted, field_name, None))
    ]
    return TaskDraft(
        title=_cut((extracted.title or "").strip(), _TITLE_MAX),
        where=_cut(extracted.where, 200) if extracted.where else None,
        body=_cut(extracted.description, _TEXT_MAX) if extracted.description else None,
        sections=sections,
        links=[TaskLink(text="Listen", url=audio_url(drive_file_id))],
        labels=[label[:_LABEL_MAX] for label in extracted.labels],
        needs_review=extracted.needs_review,
        due_on=_today(),
    )


def request_for(extracted: ExtractedTask, drive_file_id: str) -> TaskCreateRequest:
    """The whole request: the draft, keyed by the note's audio file."""
    return TaskCreateRequest(
        source=SOURCE,
        external_id=external_id_for(drive_file_id),
        # Voice-note ids are minted by the task manager.
        id=None,
        draft=draft_for(extracted, drive_file_id),
    )


def request_task(
    extracted: ExtractedTask,
    drive_file_id: str,
    *,
    api: KaianoApiClient | None = None,
) -> RequestedTask:
    """Ask the task manager for the note's task. Raises if the request did not land.

    The shared API client retries a transient failure itself; what reaches
    here is a failure the queue should redeliver, so it is raised.
    """
    request = request_for(extracted, drive_file_id)
    client = api or KaianoApiClient.from_env(MACHINE_NAME)
    _logger.info(
        "voicenotes.request_task.start",
        category="pipeline",
        context={
            "drive_file_id": drive_file_id,
            "title": request.draft.title,
            "needs_review": request.draft.needs_review,
        },
    )
    accepted = client.request_task(request)
    _logger.info(
        "voicenotes.request_task.accepted",
        category="pipeline",
        context={
            "drive_file_id": drive_file_id,
            "message_id": accepted.message_id,
        },
    )
    return RequestedTask(message_id=accepted.message_id)
