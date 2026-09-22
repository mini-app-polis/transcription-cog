"""WCS lesson transcripts: one Drive file, end to end.

Entry point: process_transcript(drive_file_id, run_id=...)

One queue message is one file. watcher-cog names each file that lands in
the configured Google Drive input folder, the API enqueues one job per
file, and the Lambda worker (``worker.py``) calls this. It used to be a
Prefect flow that swept the whole folder; a sweep of a few transcripts
outlived Lambda's 900-second ceiling, one file does not. See
docs/decisions/ADR-007-lambda-behind-sqs.md.

Filename convention (required):
    YYYY-MM-DD [instructors] > [students or organization].ext
    YYYY-MM-DD [instructors] > [students or organization] - [Topic].ext

Files that do not match the convention are skipped with a warning and
left in the input folder for manual renaming.

Steps:
    1. Find the file in the input folder. Gone means an earlier job for
       the same file already archived it: nothing to do, and not news.
    2. Validate the MIME type and the filename convention
    3. Read transcript text from Drive
    4. Guard: validate length
    5. Store raw transcript via api-kaianolevine-com → wcs_transcripts
    6. Call LLM — transcript → structured extraction JSON
    7. Validate the extraction against EXTRACTION_SCHEMA and POST to
       api-kaianolevine-com → wcs_sources. The API writes wcs_sources +
       wcs_source_extractions and runs compose_source synchronously to
       populate the canonical entity layer.
    8. Archive original file to processed folder
    9. One run report, sent however the run ends.

Retries: every call goes through a client that retries a transient
failure itself — DriveFacade, KaianoApiClient, LLMClient (PIPE-007).
A failure that outlasts those raises, and the worker hands the message
back to the queue, which redelivers it after the visibility timeout and
dead-letters it after max_receive_count.
"""

from __future__ import annotations

from importlib.metadata import PackageNotFoundError, version

import sentry_sdk
from dotenv import load_dotenv
from jsonschema import ValidationError, validate
from mini_app_polis import logger as log
from mini_app_polis.google import GoogleAPI
from mini_app_polis.llm import LLMMessage, build_llm
from mini_app_polis.llm.errors import LLMTruncationError

from ._pipeline_eval import run_report
from .api_client import SubstrateApiClient
from .config import Config, load_config
from .drive import archive_file, infer_source_type, read_transcript_text
from .filename_parser import FilenameParseError, ParsedFilename, parse_filename
from .models import (
    SourceCreatePayload,
    TranscriptCreatePayload,
)
from .prompt import PROMPT_VERSION, build_messages
from .schema import EXTRACTION_SCHEMA


def _extractor_version() -> str:
    """Return the cog's installed package version, or 'dev' if not installed."""
    try:
        return version("transcription_cog")
    except PackageNotFoundError:
        return "dev"


load_dotenv()

LOG = log.get_logger()

#: What one extraction may spend, and how many attempts it gets, passed to
#: the shared factory rather than inherited. The library's default is 60 s,
#: which is shorter than this pipeline's own calls: the run on record took
#: about five minutes end to end, most of it here, because the schema allows
#: up to 16k output tokens. The SDK's default of two retries on top would be
#: three attempts of up to ten minutes inside a 900-second invocation, so
#: retrying is left to the queue: a redelivery is a fresh invocation.
_LLM_TIMEOUT_SECONDS = 600.0
_LLM_MAX_RETRIES = 0

_SUPPORTED_MIME_TYPES = {
    "application/vnd.google-apps.document",
    "text/plain",
}


def _get_logger():
    """The shared logger. Prefect's run logger went with Prefect."""
    return LOG


def _find_in_folder(
    g: GoogleAPI, folder_id: str, drive_file_id: str
) -> tuple[str, str, str | None] | None:
    """``(file_id, name, mime_type)`` for the file, or None if it is not there.

    Listed rather than fetched by id, because "is it still in the input
    folder" is the question: a file that has left it was archived by an
    earlier job for the same file, and processing it again would re-run the
    extraction for nothing — the API would upsert the same transcript and
    replace its active extraction. The folder holds a handful of files at
    most.
    """
    for item in g.drive.get_files_in_folder(folder_id, include_folders=False):
        file_id = item.id if hasattr(item, "id") else item.get("id")
        if file_id != drive_file_id:
            continue
        name = item.name if hasattr(item, "name") else item.get("name")
        mime_type = (
            item.mime_type if hasattr(item, "mime_type") else item.get("mimeType")
        )
        return file_id, name or file_id, mime_type
    return None


def task_read_transcript(g: GoogleAPI, file_id: str, mime_type: str) -> str:
    """Read transcript text from Drive."""
    logger = _get_logger()
    logger.info(f"Reading transcript: {file_id}")
    return read_transcript_text(g, file_id, mime_type)


def task_store_transcript(
    api: SubstrateApiClient,
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


def task_call_llm(
    cfg: Config,
    transcript_text: str,
    parsed: ParsedFilename,
) -> tuple[dict, bool]:
    """Call the LLM and return (extraction, schema_valid)."""
    logger = _get_logger()
    logger.info(
        log.with_log_prefix(
            log.LOG_START,
            f"Calling LLM: provider={cfg.llm_provider} model={cfg.llm_model}",
        )
    )
    llm = build_llm(
        provider=cfg.llm_provider,
        model=cfg.llm_model,
        timeout_s=_LLM_TIMEOUT_SECONDS,
        max_retries=_LLM_MAX_RETRIES,
    )
    msg_dicts = build_messages(transcript_text, parsed=parsed)
    messages = [LLMMessage(role=m["role"], content=m["content"]) for m in msg_dicts]

    try:
        result = llm.generate_json(
            messages=messages,
            json_schema=EXTRACTION_SCHEMA,
            schema_name="extraction",
        )
    except LLMTruncationError as exc:
        logger.error(
            log.with_log_prefix(
                log.LOG_FAILURE,
                "LLM response truncated at max_tokens cap. Retries are pointless "
                f"for this error; failing fast. Increase max_tokens in flow.py "
                f"if this becomes systematic. {exc}",
            )
        )
        raise

    extraction = result.output_json

    schema_valid = True
    try:
        validate(instance=extraction, schema=EXTRACTION_SCHEMA)
    except ValidationError as exc:
        schema_valid = False
        logger.warning(
            log.with_log_prefix(
                log.LOG_WARNING,
                f"Extraction JSON failed schema validation: {exc.message}",
            )
        )

    logger.info(log.with_log_prefix(log.LOG_SUCCESS, "LLM call complete"))
    return extraction, schema_valid


def task_store_source(
    api: SubstrateApiClient,
    transcript_id: str,
    extraction: dict,
    parsed: ParsedFilename,
    cfg: Config,
) -> str:
    """Store the extracted source via POST /v1/wcs/sources.

    Builds the WcsSourceCreate payload from filename metadata + extraction
    raw_output + cog/prompt version metadata, POSTs to the substrate write
    endpoint, returns the source_id.

    The API runs compose_source synchronously inside the endpoint, so on
    successful return the canonical layer (wcs_source_attributions, etc.)
    is populated for this source.
    """
    logger = _get_logger()
    title = parsed.topic or extraction.get("title") or None
    payload = SourceCreatePayload(
        transcript_id=transcript_id,
        title=title,
        session_date=parsed.recording_date,
        session_type=parsed.session_type,  # type: ignore[arg-type]
        instructors_raw=parsed.instructors,
        students_raw=parsed.students,
        organization=parsed.organization,
        visibility="private",
        is_default_visible=False,
        extractor_version=_extractor_version(),
        extractor_model=cfg.llm_model,
        extractor_provider=cfg.llm_provider,
        prompt_version=PROMPT_VERSION,
        raw_output=extraction,
    )
    response = api.create_source(payload)
    logger.info(log.with_log_prefix(log.LOG_SUCCESS, f"Source stored: {response.id}"))
    return response.id


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
    api: SubstrateApiClient,
    cfg: Config,
    file_id: str,
    file_name: str,
    mime_type: str,
    logger,
) -> dict:
    """Process a single transcript file end to end."""

    # Step 1: validate filename convention
    try:
        parsed = parse_filename(file_name)
    except FilenameParseError as exc:
        logger.warning(
            log.with_log_prefix(
                log.LOG_WARNING,
                f"Invalid filename — skipping {file_name!r}: {exc}",
            )
        )
        sentry_sdk.capture_message(
            f"transcription-cog: invalid filename skipped: {file_name!r} — {exc}",
            level="warning",
        )
        return {"skipped": True, "reason": "invalid_filename", "file": file_name}

    # Step 2: read transcript
    raw_text = task_read_transcript(g, file_id, mime_type)
    raw_text = raw_text.strip()

    # Step 3: length guard
    if len(raw_text) < cfg.min_transcript_chars:
        logger.warning(
            log.with_log_prefix(
                log.LOG_WARNING,
                f"Transcript too short ({len(raw_text)} chars) — skipping {file_name!r}",
            )
        )
        return {"skipped": True, "reason": "transcript_too_short", "file": file_name}

    # Step 4: store raw transcript
    try:
        transcript_id = task_store_transcript(api, raw_text, file_name, file_id)
    except Exception as exc:
        if (
            "uq_wcs_transcripts_drive_file_id" in str(exc)
            or "unique" in str(exc).lower()
        ):
            logger.warning(
                log.with_log_prefix(
                    log.LOG_WARNING,
                    f"Transcript already processed, skipping: {file_name!r}",
                )
            )
            return {"skipped": True, "reason": "already_processed", "file": file_name}
        raise

    # Step 5: call LLM
    extraction, schema_valid = task_call_llm(cfg, raw_text, parsed)

    # Step 6: store source (creates/updates wcs_sources, writes active extraction,
    # runs compose_source on the API side)
    source_id = task_store_source(api, transcript_id, extraction, parsed, cfg)

    # Step 7: archive
    task_archive_file(g, file_id, cfg.notes_processed_folder_id, file_name)

    return {
        "transcript_id": transcript_id,
        "source_id": source_id,
        "file": file_name,
        "schema_valid": schema_valid,
    }


def process_transcript(drive_file_id: str, *, run_id: str | None = None) -> dict:
    """Process one transcript file from the input folder, end to end.

    ``run_id`` is the queue message id when the Lambda worker runs this,
    passed rather than resolved: ``get_run_id()`` only knew Prefect's ids.

    Raises when the file could not be processed, so the worker returns the
    message to the queue. The run report is sent first, however the run
    ends, and it names the file.

    Returns:
        Dict with counts of processed, skipped, and failed files (errors),
        plus the file's result entry — the shape the folder sweep returned,
        for one file.
    """
    logger = _get_logger()

    # notable=True because every run was asked for: watcher named this
    # file. The one quiet outcome is a file already gone from the folder,
    # which is an earlier job for the same file having finished — the
    # idempotency guard working, sent below as not notable.
    with run_report("process-transcript", notable=True, run_id=run_id) as report:
        cfg = load_config()
        g = GoogleAPI.from_env()
        api = SubstrateApiClient()

        found = _find_in_folder(g, cfg.notes_input_folder_id, drive_file_id)
        if found is None:
            logger.info(
                log.with_log_prefix(
                    log.LOG_WARNING,
                    f"File {drive_file_id} is not in the input folder — "
                    "already archived, or moved by hand. Nothing to do.",
                )
            )
            report.note("not_in_input_folder", drive_file_id)
            report.send(notable=False)
            return {
                "processed": 0,
                "skipped": 1,
                "errors": 0,
                "files": [
                    {
                        "skipped": True,
                        "reason": "not_in_input_folder",
                        "file": drive_file_id,
                    }
                ],
            }

        file_id, file_name, mime_type = found
        if mime_type not in _SUPPORTED_MIME_TYPES:
            # An issue, not a note: this file will sit in the inbox until
            # a person moves it, and nothing else in the system will ever
            # mention it.
            report.issue(
                "unsupported_file_type",
                file_name,
                detail=str(mime_type or "unknown"),
            )
            return {
                "processed": 0,
                "skipped": 1,
                "errors": 0,
                "files": [
                    {
                        "skipped": True,
                        "reason": "unsupported_file_type",
                        "file": file_name,
                    }
                ],
            }

        logger.info(log.with_log_prefix(log.LOG_START, f"Processing: {file_name!r}"))
        try:
            result = _process_one(g, api, cfg, file_id, file_name, mime_type, logger)
        except Exception as exc:
            # Named here, because the report's own record of the exception
            # carries its type and not the file. Re-raised so the message
            # goes back to the queue.
            report.issue(
                "processing_failed",
                file_name,
                detail=f"{type(exc).__name__}: {exc}",
            )
            logger.exception(
                log.with_log_prefix(
                    log.LOG_FAILURE, f"Failed processing: {file_name!r}"
                )
            )
            raise

        if result.get("skipped"):
            reason = str(result.get("reason") or "skipped")
            # already_processed is the idempotency guard doing its job —
            # counted so the totals add up, never escalated. The other two
            # mean a file was dropped and nobody will notice unless this
            # says so.
            if reason == "already_processed":
                report.note(reason, file_name)
            else:
                report.issue(reason, file_name)
            processed, skipped = 0, 1
        else:
            if result.get("schema_valid") is False:
                report.issue("schema_invalid", file_name)
            else:
                report.ok()
            # A transcript row now exists that did not before. ``ok()``
            # counts it; only this says which one, and a count is not
            # something you can go and look at.
            report.created("transcript", file_name)
            logger.info(
                log.with_log_prefix(log.LOG_SUCCESS, f"Completed: {file_name!r}")
            )
            processed, skipped = 1, 0

        return {
            "processed": processed,
            "skipped": skipped,
            "errors": 0,
            "files": [result],
        }
