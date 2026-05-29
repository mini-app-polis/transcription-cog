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
from ._pipeline_eval import make_failure_hook, post_run_finding
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


@task(retries=2)
def task_post_run_evaluation(
    *,
    processed: int,
    skipped: int,
    results: list[dict],
    errors: int,
) -> None:
    """Post ONE pipeline evaluation summarizing the whole flow run.

    Resolves PIPE-009 / PIPE-011: pipeline-cogs must emit at least one
    evaluation signal per run. The unit of evaluation is the flow run
    (not the record). Severity reflects whether every processed file
    passed schema validation and had no data-level skips.

    Run-level severity rules:
      SUCCESS: every processed file had schema_valid=True and no
               data-level skips; empty runs also SUCCESS.
      WARN:    at least one schema_valid=False, at least one data-level
               skip (invalid_filename or transcript_too_short), or at
               least one unhandled exception.
      already_processed skips are benign and do not affect severity.

    The actual POST is delegated to ``post_run_finding`` (from the
    transcription-cog shim around ``mini_app_polis.pipeline_status``);
    that helper is itself best-effort, so failure to POST never raises
    out of this task.
    """
    schema_invalid = sum(
        1 for r in results if not r.get("skipped") and r.get("schema_valid") is False
    )
    data_skips = sum(
        1
        for r in results
        if r.get("skipped")
        and r.get("reason") in {"invalid_filename", "transcript_too_short"}
    )
    benign_skips = sum(
        1
        for r in results
        if r.get("skipped") and r.get("reason") == "already_processed"
    )

    has_problems = schema_invalid > 0 or data_skips > 0 or errors > 0
    severity = "WARN" if has_problems else "SUCCESS"

    if processed == 0 and skipped == 0 and errors == 0:
        finding = "Run complete — no files to process."
    else:
        parts = [f"processed={processed}", f"skipped={skipped}"]
        if errors:
            parts.append(f"errors={errors}")
        if schema_invalid:
            parts.append(f"schema_invalid={schema_invalid}")
        if data_skips:
            parts.append(f"data_skips={data_skips}")
        if benign_skips:
            parts.append(f"already_processed={benign_skips}")
        finding = "Run complete — " + ", ".join(parts) + "."

    post_run_finding(
        "process-transcript",
        severity,
        text=finding,
        source="flow_inline",
    )


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

    with concurrency("notes-ingest", occupy=1):
        logger.info(
            log.with_log_prefix(log.LOG_START, "Scanning input folder for transcripts")
        )

        cfg = load_config()
        g = GoogleAPI.from_env()
        api = SubstrateApiClient()

        files = list(_iter_files(g, cfg.notes_input_folder_id))

        if not files:
            logger.info("No transcript files found in input folder")
            task_post_run_evaluation.with_options(
                retry_delay_seconds=cfg.task_retry_delay_short
            )(
                processed=0,
                skipped=0,
                results=[],
                errors=0,
            )
            return {"processed": 0, "skipped": 0, "files": []}

        logger.info(
            log.with_log_prefix(log.LOG_START, f"Found {len(files)} file(s) to process")
        )

        results: list[dict] = []
        processed = 0
        skipped = 0
        errors = 0
        # Saved batch-fatal exception (Prefect task timeout / cancel).
        # See transcription_cog/_batch_fatal.py — when set, finish the
        # post-run evaluation below so partial work is recorded, then
        # re-raise so the flow transitions Failed and Prefect's
        # flow-level retry picks up on the next worker.
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
                else:
                    processed += 1
                    logger.info(
                        log.with_log_prefix(
                            log.LOG_SUCCESS, f"Completed: {file_name!r}"
                        )
                    )
            except Exception as exc:
                errors += 1
                if is_batch_fatal(exc):
                    # Prefect task timeout fired / cancel signal /
                    # worker shutdown — every remaining file would
                    # fail the same way. Stop the loop, record the
                    # abort, let the run-eval post below capture
                    # partial state, then re-raise so the flow run
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

        task_post_run_evaluation.with_options(
            retry_delay_seconds=cfg.task_retry_delay_short
        )(
            processed=processed,
            skipped=skipped,
            results=results,
            errors=errors,
        )

        logger.info(
            log.with_log_prefix(
                log.LOG_SUCCESS,
                f"Run complete — processed: {processed}, skipped: {skipped}, errors: {errors}",
            )
        )

        # If a Prefect task timeout / cancel aborted the batch,
        # propagate it now (after the run eval above has recorded
        # what we did complete). Re-raising here is what transitions
        # the flow run to Failed so the @flow-level retry kicks in
        # cleanly on the next worker, rather than the cog quietly
        # returning a partial-success summary on an infra-level
        # failure.
        if batch_fatal_exc is not None:
            raise batch_fatal_exc

        return {
            "processed": processed,
            "skipped": skipped,
            "errors": errors,
            "files": results,
        }
