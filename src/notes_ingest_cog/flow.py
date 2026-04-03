"""Prefect flow for notes-ingest-cog.

Entry point: process_transcript()

Triggered by watcher-cog when a new transcript file lands in the
configured Google Drive input folder. Scans the folder and processes
all files found. No parameters required — all config comes from env vars
via Doppler → Railway.

Flow steps (per file):
  1. Scan input folder for supported transcript files
  2. Read transcript text from Drive
  3. Guard: validate length
  4. Store raw transcript via api-kaianolevine-com → wcs_transcripts
  5. Call LLM — transcript → structured notes JSON
  6. Validate and store notes via api-kaianolevine-com → wcs_notes
  7. Archive original file to processed folder
"""

from __future__ import annotations

from dotenv import load_dotenv
from jsonschema import ValidationError, validate
from mini_app_polis import logger as log
from mini_app_polis.google import GoogleAPI
from mini_app_polis.llm import LLMMessage, build_llm
from prefect import flow, get_run_logger, task

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


def _get_logger():
    """Dual logger pattern per PIPE-006."""
    try:
        return get_run_logger()
    except Exception:
        return LOG


def _coerce_session_type(raw: str | None) -> SessionType:
    """Coerce LLM session_type output to a valid SessionType literal."""
    if raw and raw in _VALID_SESSION_TYPES:
        return raw  # type: ignore[return-value]
    _LEGACY_MAP: dict[str, SessionType] = {
        "group_class": "class_attended",
        "coaching": "coaching_session",
    }
    if raw and raw in _LEGACY_MAP:
        return _LEGACY_MAP[raw]
    return "other"


def _iter_files(g: GoogleAPI, folder_id: str):
    """Yield (file_id, file_name, mime_type) for supported files in folder."""
    for item in g.drive.get_files_in_folder(folder_id, include_folders=False):
        mime_type = (
            item.mime_type if hasattr(item, "mime_type") else item.get("mimeType")
        )
        file_id = item.id if hasattr(item, "id") else item.get("id")
        name = item.name if hasattr(item, "name") else item.get("name")
        if mime_type in _SUPPORTED_MIME_TYPES and file_id:
            yield file_id, name or file_id, mime_type


@task(retries=2, retry_delay_seconds=30)
def task_read_transcript(g: GoogleAPI, file_id: str, mime_type: str) -> str:
    """Read transcript text from Drive."""
    logger = _get_logger()
    logger.info(f"Reading transcript: {file_id}")
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
def task_call_llm(cfg: Config, transcript_text: str, source_filename: str) -> dict:
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

    try:
        validate(instance=notes, schema=NOTES_SCHEMA)
    except ValidationError as exc:
        logger.warning(
            log.with_log_prefix(
                log.LOG_WARNING,
                f"Notes JSON failed schema validation: {exc.message}",
            )
        )

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
        visibility="private",
        model=cfg.llm_model,
        provider=cfg.llm_provider,
        notes_json=notes,
    )
    response = api.create_note(payload)
    logger.info(log.with_log_prefix(log.LOG_SUCCESS, f"Notes stored: {response.id}"))
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


def _process_one(
    g: GoogleAPI,
    api: NotesApiClient,
    cfg: Config,
    file_id: str,
    file_name: str,
    mime_type: str,
    logger,
) -> dict:
    """Process a single transcript file end to end."""
    raw_text = task_read_transcript(g, file_id, mime_type)
    raw_text = raw_text.strip()

    if len(raw_text) < cfg.min_transcript_chars:
        logger.warning(
            log.with_log_prefix(
                log.LOG_WARNING,
                f"Transcript too short ({len(raw_text)} chars) — skipping {file_name!r}",
            )
        )
        return {"skipped": True, "reason": "transcript_too_short", "file": file_name}

    transcript_id = task_store_transcript(api, raw_text, file_name, file_id)
    notes = task_call_llm(cfg, raw_text, file_name)
    note_id = task_store_notes(api, transcript_id, notes, cfg)
    task_archive_file(g, file_id, cfg.notes_processed_folder_id, file_name)

    return {"transcript_id": transcript_id, "note_id": note_id, "file": file_name}


@flow(
    name="process-transcript",
    description=(
        "Scan the WCS notes input folder and process all transcript files found. "
        "No parameters required — triggered by watcher-cog when new files are dropped."
    ),
)
def process_transcript() -> dict:
    """Main Prefect flow — scans input folder and processes all transcripts found.

    All configuration comes from environment variables via Doppler → Railway.
    Triggered by watcher-cog; can also be run manually from Prefect UI with no input.

    Returns:
        Dict with counts of processed and skipped files.
    """
    logger = _get_logger()
    logger.info(
        log.with_log_prefix(log.LOG_START, "Scanning input folder for transcripts")
    )

    cfg = load_config()
    g = GoogleAPI.from_env()
    api = NotesApiClient(
        base_url=cfg.kaiano_api_base_url,
        internal_key=cfg.kaiano_api_internal_key,
    )

    files = list(_iter_files(g, cfg.notes_input_folder_id))

    if not files:
        logger.info("No transcript files found in input folder")
        return {"processed": 0, "skipped": 0, "files": []}

    logger.info(
        log.with_log_prefix(log.LOG_START, f"Found {len(files)} file(s) to process")
    )

    results = []
    processed = 0
    skipped = 0

    for file_id, file_name, mime_type in files:
        logger.info(log.with_log_prefix(log.LOG_START, f"Processing: {file_name!r}"))
        try:
            result = _process_one(g, api, cfg, file_id, file_name, mime_type, logger)
            results.append(result)
            if result.get("skipped"):
                skipped += 1
            else:
                processed += 1
                logger.info(
                    log.with_log_prefix(log.LOG_SUCCESS, f"Completed: {file_name!r}")
                )
        except Exception:
            logger.exception(
                log.with_log_prefix(
                    log.LOG_FAILURE, f"Failed processing: {file_name!r}"
                )
            )
            skipped += 1

    logger.info(
        log.with_log_prefix(
            log.LOG_SUCCESS,
            f"Run complete — processed: {processed}, skipped: {skipped}",
        )
    )
    return {"processed": processed, "skipped": skipped, "files": results}
