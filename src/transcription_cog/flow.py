"""Prefect flow for transcription-cog.

Entry point: process_transcript()

Triggered by watcher-cog when a new transcript file lands in the
configured Google Drive input folder. Scans the folder and processes
all files found. No parameters required — all config comes from env vars
via Doppler → Railway.

Filename convention (required):
    YYYY-MM-DD [instructors] > [students or organization].ext
    YYYY-MM-DD [instructors] > [students or organization] - [Topic].ext

Files that do not match the convention are skipped with a warning and
left in the input folder for manual renaming.

Flow steps:
  Per file:
    1. Scan input folder for supported transcript files
    2. Validate filename against naming convention
    3. Read transcript text from Drive
    4. Guard: validate length
    5. Store raw transcript via api-kaianolevine-com → wcs_transcripts
    6. Call LLM — transcript → structured extraction JSON
    7. Validate the extraction against EXTRACTION_SCHEMA and POST to
       api-kaianolevine-com → wcs_sources. The API writes wcs_sources +
       wcs_source_extractions and runs compose_source synchronously to
       populate the canonical entity layer.
    8. Archive original file to processed folder
  Per run (once, after all files):
    9. Post one pipeline evaluation to pipeline_evaluations summarizing
       the whole run (best-effort).
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
from prefect import flow, get_run_logger, task
from prefect.concurrency.sync import concurrency

from ._batch_fatal import is_batch_fatal
from ._pipeline_eval import make_failure_hook, run_report
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

_SUPPORTED_MIME_TYPES = {
    "application/vnd.google-apps.document",
    "text/plain",
}


def _get_logger():
    """Dual logger pattern per PIPE-006."""
    try:
        return get_run_logger()
    except Exception:
        return LOG


def _iter_files(g: GoogleAPI, folder_id: str) -> tuple[list[tuple], list[tuple]]:
    """Split a folder into files this flow can process and files it cannot.

    Returns ``(supported, rejected)``. Rejections used to be a bare
    ``continue`` inside a generator, which meant a .docx or .pdf dropped
    in the inbox produced a run reporting "processed: 0, skipped: 0" —
    a clean sweep of a folder that was not empty. The file is not
    archived either, so it stays there and the same silent run repeats
    every time watcher-cog fires.
    """
    supported: list[tuple] = []
    rejected: list[tuple] = []
    for item in g.drive.get_files_in_folder(folder_id, include_folders=False):
        mime_type = (
            item.mime_type if hasattr(item, "mime_type") else item.get("mimeType")
        )
        file_id = item.id if hasattr(item, "id") else item.get("id")
        name = item.name if hasattr(item, "name") else item.get("name")
        if not file_id:
            rejected.append(("", name or "<unnamed>", mime_type))
            continue
        if mime_type in _SUPPORTED_MIME_TYPES:
            supported.append((file_id, name or file_id, mime_type))
        else:
            rejected.append((file_id, name or file_id, mime_type))
    return supported, rejected


@task(retries=2)
def task_read_transcript(g: GoogleAPI, file_id: str, mime_type: str) -> str:
    """Read transcript text from Drive."""
    logger = _get_logger()
    logger.info(f"Reading transcript: {file_id}")
    return read_transcript_text(g, file_id, mime_type)


@task(retries=2)
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


@task(retries=2)
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
    llm = build_llm(provider=cfg.llm_provider, model=cfg.llm_model)
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


@task(retries=2)
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
    raw_text = task_read_transcript.with_options(
        retry_delay_seconds=cfg.task_retry_delay_short
    )(g, file_id, mime_type)
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
        transcript_id = task_store_transcript.with_options(
            retry_delay_seconds=cfg.task_retry_delay_short
        )(api, raw_text, file_name, file_id)
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
    extraction, schema_valid = task_call_llm.with_options(
        retry_delay_seconds=cfg.task_retry_delay_long
    )(cfg, raw_text, parsed)

    # Step 6: store source (creates/updates wcs_sources, writes active extraction,
    # runs compose_source on the API side)
    source_id = task_store_source.with_options(
        retry_delay_seconds=cfg.task_retry_delay_short
    )(api, transcript_id, extraction, parsed, cfg)

    # Step 7: archive
    task_archive_file(g, file_id, cfg.notes_processed_folder_id, file_name)

    return {
        "transcript_id": transcript_id,
        "source_id": source_id,
        "file": file_name,
        "schema_valid": schema_valid,
    }


# Pre-built Prefect on_failure / on_crashed hook that posts a single
# flow_hook finding (WARN for Failed, ERROR for Crashed). The body lives
# in mini_app_polis.pipeline_status; this cog just supplies its name and
# repo identity. Replaces the old hand-rolled _emit_terminal_failure
# which talked to SubstrateApiClient.post_run_evaluation directly.
_emit_terminal_failure = make_failure_hook("process-transcript")


@flow(
    name="process-transcript",
    description=(
        "Scan the WCS notes input folder and process all transcript files found. "
        "No parameters required — triggered by watcher-cog when new files are dropped. "
        "Files must follow the naming convention: "
        "'YYYY-MM-DD Instructor > Student/Org - Topic.ext'"
    ),
    on_failure=[_emit_terminal_failure],
    on_crashed=[_emit_terminal_failure],
)
def process_transcript() -> dict:
    """Main Prefect flow — scans input folder and processes all transcripts found.

    All configuration comes from environment variables via Doppler → Railway.
    Triggered by watcher-cog; can also be run manually from Prefect UI with no input.

    The concurrency slot 'notes-ingest' (limit 1) ensures only one run can hold
    the folder scan and processing lock at a time. A second run triggered while
    the first is active will block at the slot until the first run completes,
    including archiving all files. This prevents duplicate LLM calls on the same
    file when watcher-cog fires mid-run.

    Returns:
        Dict with counts of processed, skipped, and failed files (errors),
        plus per-file result entries.
    """
    logger = _get_logger()

    # notable=True because this deployment has no cron — it runs only
    # because watcher-cog fired it, so every run had a reason and none of
    # them is an idle tick to keep quiet. That includes a run that finds
    # nothing: the watcher saying "2 new files" and this flow finding none
    # is a mismatch, and it is invisible unless the empty run says so.
    with (
        concurrency("notes-ingest", occupy=1),
        run_report("process-transcript", notable=True) as report,
    ):
        logger.info(
            log.with_log_prefix(log.LOG_START, "Scanning input folder for transcripts")
        )

        cfg = load_config()
        g = GoogleAPI.from_env()
        api = SubstrateApiClient()

        files, rejected = _iter_files(g, cfg.notes_input_folder_id)

        for _fid, rejected_name, rejected_mime in rejected:
            # An issue, not a note: this file will sit in the inbox until
            # a person moves it, and nothing else in the system will ever
            # mention it.
            report.issue(
                "unsupported_file_type",
                rejected_name,
                detail=str(rejected_mime or "unknown"),
            )

        if not files:
            logger.info("No transcript files found in input folder")
            return {"processed": 0, "skipped": 0, "files": []}

        logger.info(
            log.with_log_prefix(log.LOG_START, f"Found {len(files)} file(s) to process")
        )

        results: list[dict] = []
        processed = 0
        skipped = 0
        errors = 0
        # Saved batch-fatal exception (Prefect task timeout / cancel).
        # See transcription_cog/_batch_fatal.py — when set, stop the loop
        # and re-raise below so the flow transitions Failed and Prefect's
        # flow-level retry picks up on the next worker. The report records
        # the exception and sends partial work on its way out.
        batch_fatal_exc: BaseException | None = None

        for file_id, file_name, mime_type in files:
            logger.info(
                log.with_log_prefix(log.LOG_START, f"Processing: {file_name!r}")
            )
            try:
                result = _process_one(
                    g, api, cfg, file_id, file_name, mime_type, logger
                )
                results.append(result)
                if result.get("skipped"):
                    skipped += 1
                    reason = str(result.get("reason") or "skipped")
                    # already_processed is the idempotency guard doing its
                    # job — counted so the totals add up, never escalated.
                    # The other two mean a file was dropped and nobody
                    # will notice unless this says so.
                    if reason == "already_processed":
                        report.note(reason, file_name)
                    else:
                        report.issue(reason, file_name)
                else:
                    processed += 1
                    if result.get("schema_valid") is False:
                        report.issue("schema_invalid", file_name)
                    else:
                        report.ok()
                    logger.info(
                        log.with_log_prefix(
                            log.LOG_SUCCESS, f"Completed: {file_name!r}"
                        )
                    )
            except Exception as exc:
                errors += 1
                report.issue(
                    "processing_failed",
                    file_name,
                    detail=f"{type(exc).__name__}: {exc}",
                )
                if is_batch_fatal(exc):
                    # Prefect task timeout fired / cancel signal /
                    # worker shutdown — every remaining file would
                    # fail the same way. Stop the loop, record the
                    # abort, then re-raise below so the flow run
                    # transitions Failed cleanly.
                    logger.exception(
                        log.with_log_prefix(
                            log.LOG_FAILURE,
                            f"Batch aborted at: {file_name!r}",
                        )
                    )
                    batch_fatal_exc = exc
                    break
                logger.exception(
                    log.with_log_prefix(
                        log.LOG_FAILURE, f"Failed processing: {file_name!r}"
                    )
                )

        logger.info(
            log.with_log_prefix(
                log.LOG_SUCCESS,
                f"Run complete — processed: {processed}, skipped: {skipped}, errors: {errors}",
            )
        )

        # If a Prefect task timeout / cancel aborted the batch, propagate
        # it now. It passes through the report's context manager, which
        # sends what the run did complete before re-raising. Re-raising is
        # what transitions the flow run to Failed so the @flow-level retry
        # kicks in cleanly on the next worker, rather than the cog quietly
        # returning a partial-success summary on an infra-level failure.
        if batch_fatal_exc is not None:
            raise batch_fatal_exc

        return {
            "processed": processed,
            "skipped": skipped,
            "errors": errors,
            "files": results,
        }
