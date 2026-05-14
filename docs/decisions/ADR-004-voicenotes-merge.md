# ADR-004: Merge voicenotes-cog into notes-ingest-cog

**Status:** Accepted (May 2026)

## Context

Two adjacent pipelines lived in separate repositories, each consuming
one Prefect Cloud deployment slot:

- `notes-ingest-cog` — WCS lesson transcripts → structured notes → Postgres
- `voicenotes-cog` — voice sticky-notes → Whisper → Claude → Todoist

Prefect Cloud's free tier caps the workspace at five deployments. Each
cog also paid for its own Railway service, Sentry project, and CI
release cycle, despite sharing the same observability stack and being
maintained by the same operator.

The same consolidation pattern was applied earlier to `deejay-cog`,
which now hosts multiple flows (`process-new-files`,
`ingest-live-history`, etc.) behind a single router-style deployment
named `deejay-cog/deejay-cog`.

## Decision

Move all voicenotes-cog functionality into notes-ingest-cog under a
new sub-package `notes_ingest_cog.voicenotes`, and expose both
pipelines through a single Prefect deployment
(`notes-ingest-cog/notes-ingest-cog`) backed by a router flow that
dispatches on a `mode` parameter:

| `mode`               | Underlying flow              | Source pipeline                            |
| -------------------- | ---------------------------- | ------------------------------------------ |
| `wcs-transcripts`    | `process_transcript`         | original notes-ingest-cog (WCS)            |
| `voicenotes`         | `voicenotes_ingest`          | merged voicenotes-cog (Whisper → Todoist)  |
| `voicenotes-cleanup` | `voicenotes_cleanup`         | merged voicenotes-cog (operator sweep)     |

The router and dispatch table live in `src/notes_ingest_cog/main.py`.
Mirrors deejay-cog's pattern exactly.

## Consequences

**Saved:**
- One Prefect Cloud deployment slot
- One Railway service
- One Sentry project (voicenotes errors now route to
  `SENTRY_DSN_NOTES_INGEST_COG`; `SENTRY_DSN_VOICENOTES` is no longer
  read by the runtime)
- One CI release pipeline

**Cost:**
- watcher-cog must be reconfigured: the voice-inbox watcher entry now
  fires `notes-ingest-cog/notes-ingest-cog` with
  `{"mode": "voicenotes"}` instead of the old
  `voicenotes-cog/voicenotes` deployment. Same for the wcs-notes
  watcher (now passes `{"mode": "wcs-transcripts"}`).
- A single deployment with `concurrency_limit=1` serializes the two
  pipelines: a long voicenotes run delays a wcs-transcripts trigger
  and vice versa. Acceptable at current volume (a handful of files
  per day across both pipelines). If contention shows up, drop
  `concurrency_limit` on the deployment and rely on each flow's
  in-flow Prefect runtime concurrency slot (`notes-ingest`,
  `voicenotes-cog`) instead.
- Telemetry continuity: the voicenotes sub-pipeline still reports
  `repo="voicenotes-cog"` and `flow_name="voicenotes-ingest"` in
  `pipeline_evaluations` rows so the Pipeline Health dashboard
  history is preserved. The `notes-ingest-cog` repo identifier
  continues to be used for the WCS pipeline.
- Test isolation: `tests/voicenotes/conftest.py` deliberately does
  NOT enter `prefect_test_harness` — that's provided session-wide
  by the parent `tests/conftest.py`. Re-entering it would race two
  harnesses on the same temporary backend.

## Migration steps

1. Source moved from `voicenotes-cog/src/voicenotes_cog/` to
   `notes-ingest-cog/src/notes_ingest_cog/voicenotes/`. All imports
   rewritten from `voicenotes_cog.*` to `notes_ingest_cog.voicenotes.*`.
2. New router flow + dispatch table added to
   `notes_ingest_cog.main` (replacing the previous direct-serve of
   `process_transcript`).
3. Dependencies merged into the root `pyproject.toml`: `anthropic`,
   `openai`, `httpx`, `google-api-python-client`, `google-auth`,
   `pydantic-settings`.
4. `.env.example` extended with the voicenotes section.
5. Tests moved to `tests/voicenotes/` (preserved unit/integration
   split). `tests/voicenotes/conftest.py` retains the env bootstrap
   and singleton-reset behavior; the duplicate `prefect_test_harness`
   fixture was removed in favor of the parent's.
6. README and this ADR updated.
7. `voicenotes-cog` repo to be archived by the operator separately
   (not touched by this migration).
