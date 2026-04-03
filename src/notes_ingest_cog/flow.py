"""Prefect flow for notes-ingest-cog.

Entry point: process_transcript(file_id)

Triggered by watcher-cog when a new transcript file lands in the
configured Google Drive input folder. Can also be triggered manually
from the Prefect UI during development.

Flow steps:
  1. Read transcript text from Drive
  2. Validate length
  3. Store raw transcript via api-kaianolevine-com → wcs_transcripts
  4. Build LLM messages and call configured provider
  5. Validate LLM output against JSON schema
  6. Store structured notes via api-kaianolevine-com → wcs_notes
  7. Archive original file to processed folder
"""

from __future__ import annotations

import json

from dotenv import load_dotenv
from jsonschema import validate, ValidationError
from mini_app_polis import logger as log
from mini_app_polis.google import GoogleAPI
from mini_app_polis.llm import LLMMessage, build_llm
from prefect import flow, task, get_run_logger

from .api_client import NotesApiClient
from .config import Config, load_config
from .drive import archive_file, infer_source_type, read_transcript_text
from .models import (
    NoteCreatePayload,
    SessionType,
    TranscriptCreatePayload,
)
from .prompt import build_messages
from .schema import NOTES_SCHEMA

load_dotenv()

LOG = log.get_logger()

_SUPPORTED_MIME_TYPES = {
    "application/vnd.google-apps.document",
    "text/plain",
}

_VALID_SESSION_TYPES: set[str] = {
    "private_lesson",
    "class_taught",
    "class_attended",
    "workshop",
    "coaching_session",
    "other",
}


def _get_logger():  # type: ignore[return]
    """Dual logger pattern per PIPE-006."""
    try:
        return get_run_logger()
    except Exception:
        return LOG


def _coerce_session_type(raw: str | None) -> SessionType:
    """Coerce LLM session_type output to a valid SessionType literal."""
    if raw and raw in _VALID_SESSION_TYPES:
        return raw  # type: ignore[return-value]
    # Map old schema values to new
    _LEGACY_MAP: dict[str, SessionType] = {
        "group_class": "class_attended",
        "coaching": "coaching_session",
    }
    if raw and raw in _LEGACY_MAP:
        return _LEGACY_MAP[raw]
    return "other"


@task(retries=2, retry_delay_seconds=30)
def task_read_transcript(
    g: GoogleAPI,
    file_id: str,
    mime_type: str,
) -> str:
    """Read transcript text from Drive."""
    logger = _get_logger()
    logger.info(f"Reading transcript from Drive: {file_id}")
    return read_transcript_text(g, file_id, mime_type)


@task(retries=2, retry_delay_seconds=30)
def task_store_transcript(
    api: NotesApiClient,
    raw_text: str,
    source_filename: str,
    drive_file_id: str,
) -> str:
    """Store raw transcript and return transcript_id."""
    logger = _get_logger()
    source_type = infer_source_type(source_filename)
    logger.info(
        log.with_log_prefix(log.LOG_START, f"Storing transcript: {source_filename}")
    )
    payload = TranscriptCreatePayload(
        raw_text=raw_text,
        source_type=source_type,  # type: ignore[arg-type]
        source_filename=source_filename,
        drive_file_id=drive_file_id,
    )
    response = api.create_transcript(payload)
    logger.info(f"Transcript stored: {response.id}")
    return response.id


@task(retries=2, retry_delay_seconds=60)
def task_call_llm(
    cfg: Config,
    transcript_text: str,
    source_filename: str,
) -> dict:
    """Call the LLM and return validated notes JSON."""
    logger = _get_logger()
    logger.info(
        log.with_log_prefix(
            log.LOG_START,
            f"Calling LLM: provider={cfg.llm_provider} model={cfg.llm_model}",
        )
    )
    llm = build_llm(provider=cfg.llm_provider, model=cfg.llm_model)
    msg_dicts = build_messages(transcript_text, source_filename=source_filename)
    messages = [LLMMessage(role=m["role"], content=m["content"]) for m in msg_dicts]

    result = llm.generate_json(
        messages=messages,
        json_schema=NOTES_SCHEMA,
        schema_name="notes",
    )
    notes = result.output_json

    # Validate against schema
    try:
        validate(instance=notes, schema=NOTES_SCHEMA)
    except ValidationError as exc:
        logger.warning(
            log.with_log_prefix(
                log.LOG_WARNING,
                f"Notes JSON failed schema validation: {exc.message}",
            )
        )
        # Continue — additionalProperties:True means partial output is still usable

    logger.info(log.with_log_prefix(log.LOG_SUCCESS, "LLM call complete"))
    return notes


@task(retries=2, retry_delay_seconds=30)
def task_store_notes(
    api: NotesApiClient,
    transcript_id: str,
    notes: dict,
    cfg: Config,
) -> str:
    """Store structured notes and return note_id."""
    logger = _get_logger()
    session_type = _coerce_session_type(notes.get("session_type"))
    payload = NoteCreatePayload(
        transcript_id=transcript_id,
        title=notes.get("title"),
        session_date=notes.get("date"),
        session_type=session_type,
        visibility="private",  # default — user can promote to public later
        model=cfg.llm_model,
        provider=cfg.llm_provider,
        notes_json=notes,
    )
    response = api.create_note(payload)
    logger.info(
        log.with_log_prefix(log.LOG_SUCCESS, f"Notes stored: {response.id}")
    )
    return response.id


@task
def task_archive_file(
    g: GoogleAPI,
    file_id: str,
    processed_folder_id: str,
    name: str,
) -> None:
    """Archive the processed transcript file."""
    archive_file(g, file_id, processed_folder_id, name)


@flow(
    name="process-transcript",
    description="Process a single WCS lesson transcript from Drive into structured notes.",
)
def process_transcript(
    file_id: str,
    file_name: str = "",
    mime_type: str = "text/plain",
) -> dict:
    """Main Prefect flow — one transcript in, one note record out.

    Args:
        file_id:   Google Drive file ID of the transcript to process.
        file_name: Original filename (used for source inference and LLM context).
        mime_type: MIME type of the Drive file.

    Returns:
        Dict with transcript_id and note_id on success.
    """
    logger = _get_logger()
    logger.info(
        log.with_log_prefix(
            log.LOG_START,
            f"Processing transcript: file_id={file_id} name={file_name!r}",
        )
    )

    cfg = load_config()
    g = GoogleAPI.from_env()
    api = NotesApiClient(
        base_url=cfg.kaiano_api_base_url,
        internal_key=cfg.kaiano_api_internal_key,
    )

    # Guard: unsupported MIME type
    if mime_type not in _SUPPORTED_MIME_TYPES:
        logger.warning(
            log.with_log_prefix(
                log.LOG_WARNING,
                f"Unsupported MIME type {mime_type!r} for file {file_name!r} — skipping",
            )
        )
        return {"skipped": True, "reason": f"unsupported_mime_type:{mime_type}"}

    # Step 1: Read transcript
    raw_text = task_read_transcript(g, file_id, mime_type)
    raw_text = raw_text.strip()

    # Guard: transcript too short
    if len(raw_text) < cfg.min_transcript_chars:
        logger.warning(
            log.with_log_prefix(
                log.LOG_WARNING,
                f"Transcript too short ({len(raw_text)} chars) — skipping {file_name!r}",
            )
        )
        return {"skipped": True, "reason": "transcript_too_short"}

    # Step 2: Store raw transcript
    transcript_id = task_store_transcript(api, raw_text, file_name, file_id)

    # Step 3: Call LLM
    notes = task_call_llm(cfg, raw_text, file_name)

    # Step 4: Store structured notes
    note_id = task_store_notes(api, transcript_id, notes, cfg)

    # Step 5: Archive original file
    task_archive_file(g, file_id, cfg.notes_processed_folder_id, file_name)

    logger.info(
        log.with_log_prefix(
            log.LOG_SUCCESS,
            f"Transcript processed: transcript_id={transcript_id} note_id={note_id}",
        )
    )
    return {"transcript_id": transcript_id, "note_id": note_id}
