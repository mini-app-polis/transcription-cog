# Pipeline — transcription-cog

## Where this cog fits in the ecosystem

```
Plaud AI / other source
        │
        ▼ (manual drop)
Google Drive — input folder (NOTES_INPUT_FOLDER_ID)
        │
        ▼ (1-minute poll)
watcher-cog
        │
        ▼ POST /v1/transcription/runs {mode, drive_file_id}, once per file
api-kaianolevine-com
        │
        ▼ SQS transcription-jobs.fifo (one message = one file)
transcription-cog  ◄─── THIS COG (Lambda)
        │
        ├─► api-kaianolevine-com  POST /v1/wcs/transcripts  →  wcs_transcripts table
        │
        ├─► api-kaianolevine-com  POST /v1/wcs/sources      →  wcs_sources + wcs_source_extractions
        │
        └─► Google Drive — processed folder (NOTES_PROCESSED_FOLDER_ID)
                                              (original file archived here)

wcs.kaianolevine.com  ◄─── reads from api-kaianolevine-com /v1/wcs/notes
```

## Flow steps

0. **Find the file** — still in the input folder? Gone means an earlier job
   for the same file archived it; nothing to do.
1. **Validate** — supported MIME type, and the filename convention
   (`YYYY-MM-DD [instructors] > [students or organization][ - Topic].ext`);
   a file that fails either is left in the input folder
2. **Read transcript** — fetch raw text from Drive (Google Doc or .txt)
3. **Guard: length** — skip if below MIN_TRANSCRIPT_CHARS (default 200), or MIN_NOTE_CHARS (default 20) for an authored note

   An **authored note** is a file whose right-hand side is the word `Note` or
   `Correction` (singular or plural; both mean the same):
   `2026-10-16 Kaiano > Note - Anchor is an arrival.txt`. It is a deliberate
   addition or correction by the instructor on the left, ingested exactly like a
   lesson (session type `correction`, no students), with prompt instructions to record
   corrections as such, and to record identity fixes as relations: `alias_of`
   (same thing, other name), `facet_of` (a detail of a broader concept) and
   `distinct_from` (keep apart; undoes either). Newer statements win downstream
   (wiki-curator-cog ADR-005).

   A note can also be written on the site, from a wiki page. The API
   sends it as a `wcs-note` message carrying the text, and `process_note` takes
   it through steps 3–6 under the name `<date> <instructor> > Note - <title>`,
   with source type `manual` and the id the API derived from the note's content
   as its `drive_file_id`. There is no Drive file to find, read or archive.
4. **Store transcript** — POST raw text to `/v1/wcs/transcripts`, get `transcript_id`
5. **Call LLM** — send transcript through structured prompt, get JSON extraction validated against `EXTRACTION_SCHEMA`
6. **Store source** — POST to `/v1/wcs/sources`, get `source_id`; the API writes the source and its active extraction and runs `compose_source`
7. **Archive** — move original Drive file to processed folder

## Trigger

`watcher-cog` detects new and modified files in `NOTES_INPUT_FOLDER_ID` and
asks the API for one run per file: `POST /v1/transcription/runs` with
`{"mode": "wcs-transcripts", "drive_file_id": ...}`. The API enqueues onto
`transcription-jobs.fifo`, and the Lambda worker runs `process_transcript` with
the file id and the queue message id as the run id. watcher asks again every
minute while a file is still in the folder; the API's dispatch claims make the
repeats one job.

A run that fails reports itself and raises; the queue redelivers it after
the visibility timeout and dead-letters it after three receives.

During development the flow can be run directly —
`process_transcript("<drive file id>")` — with env vars set.

## Supported file types

| MIME type | Source |
|-----------|--------|
| `text/plain` | Plaud AI export, Otter, manual |
| `application/vnd.google-apps.document` | Google Docs |

## Source type inference

Source type is inferred from the filename:
- `plaud` — filename contains "plaud"
- `otter` — filename contains "otter"
- `zoom` — filename contains "zoom"
- `google_meet` — filename contains "meet"
- `unknown` — no match

## Storage

Tables on `api-kaianolevine-com`'s Railway Postgres:

**`wcs_transcripts`**
- `id` — UUID primary key
- `raw_text` — full transcript text (retained for future RAG/embeddings)
- `source_type` — inferred from filename
- `source_filename` — original Drive filename
- `drive_file_id` — Google Drive file ID
- `created_at`

**`wcs_sources`**
- `id` — UUID primary key
- `transcript_id` — FK to `wcs_transcripts`
- `title` — the filename's topic, else extracted by LLM
- `session_date` — from the filename
- `session_type` — from the filename: `private_lesson | group_class`
- `instructors_raw`, `students_raw`, `organization` — from the filename
- `visibility` — written as `private`; `is_default_visible` written as `false`
- `created_at`, `updated_at`

**`wcs_source_extractions`**
- `source_id` — FK to `wcs_sources`
- `extractor_version`, `extractor_model`, `extractor_provider`, `prompt_version`
- `raw_output` — the LLM extraction JSON
- `is_active` — the extraction in use; a re-run replaces it
- `extracted_at`

## Observability

| Layer | Mechanism |
|-------|-----------|
| L1 Liveness | `transcription-dlq-not-empty` CloudWatch alarm, with an email action |
| L2 Logs | `mini_app_polis` logger — structured JSON in production |
| L3 Exceptions | Sentry — SENTRY_DSN_COGS, initialised before any app logic |
| L4 Run history | One run report per run to Discord, under the queue message id |
