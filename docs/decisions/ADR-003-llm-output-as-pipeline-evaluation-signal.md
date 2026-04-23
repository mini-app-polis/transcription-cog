# ADR-003: Use LLM schema validity as the pipeline_evaluations signal

Date: 2026-04-20

## Status

Accepted

## Context

PIPE-009 / PIPE-011 in `ecosystem-standards` require that every
pipeline-cog emits at least one evaluation record to `pipeline_evaluations`
per processed artifact, so ecosystem-wide quality can be tracked uniformly.

`notes-ingest-cog` is not itself an evaluator — it's a data pipeline
(transcript → notes). The question is what "evaluation" means here.

## Decision

Treat LLM output schema validity as the pipeline's quality signal. After
`task_call_llm`, record whether the output validated against `NOTES_SCHEMA`.
After `task_archive_file` (end of per-file pipeline), post a single
evaluation finding:

- `severity=SUCCESS` if the schema validated
- `severity=WARN` if it did not
- `source=notes-ingest-cog`, `source_ref=<transcript_id>`
- `dimension=llm_output_quality`
- `meta` includes `llm_provider`, `llm_model`, and structural flags
  (`has_title`, `has_summary`) for downstream dashboards

Evaluation posting is best-effort. `task_post_evaluation` catches and
logs its own exceptions rather than failing the pipeline — a dropped
evaluation finding is less bad than a failed note ingest.

## Consequences

- Pipeline quality becomes visible in the ecosystem evaluations dashboard
  without building a separate evaluator.
- `task_call_llm` now returns `(notes, schema_valid)` instead of `notes`.
  Callers updated.
- When an LLM output partially validates or validates with warnings, the
  signal is binary — WARN or SUCCESS — which may under-report. If finer
  grain is needed later, the `meta` bag can carry per-field validity and
  the severity can move to the full five-value scale
  (CRITICAL/ERROR/WARN/INFO/SUCCESS) once the API constraint lands.
- `NotesApiClient.post_evaluation` mirrors the existing `create_transcript`
  pattern until a shared `EvaluationClient` ships in common-python-utils.
