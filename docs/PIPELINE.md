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
        ▼ SQS transcription-jobs (one message = one file)
transcription-cog  ◄─── THIS COG (Lambda)
        │
        ├─► api-kaianolevine-com  POST /v1/wcs/transcripts  →  wcs_transcripts table
        │
        ├─► api-kaianolevine-com  POST /v1/wcs/notes        →  wcs_notes table
        │
        └─► Google Drive — processed folder (NOTES_PROCESSED_FOLDER_ID)
                                              (original file archived here)

wcs.kaianolevine.com  ◄─── reads from api-kaianolevine-com /v1/wcs/notes
```

## Flow steps

0. **Find the file** — still in the input folder? Gone means an earlier job
   for the same file archived it; nothing to do.
1. **Read transcript** — fetch raw text from Drive (Google Doc or .txt)
2. **Guard: length** — skip if below MIN_TRANSCRIPT_CHARS (default 200)
3. **Store transcript** — POST raw text to `/v1/wcs/transcripts`, get `transcript_id`
4. **Call LLM** — send transcript through structured prompt, get validated JSON notes
5. **Store notes** — POST structured notes to `/v1/wcs/notes`, get `note_id`
6. **Archive** — move original Drive file to processed folder

## Trigger

`watcher-cog` detects new and modified files in `NOTES_INPUT_FOLDER_ID` and
asks the API for one run per file: `POST /v1/transcription/runs` with
`{"mode": "wcs-transcripts", "drive_file_id": ...}`. The API enqueues onto
`transcription-jobs`, and the Lambda worker runs `process_transcript` with
the file id and the queue message id as the run id. On startup watcher also
asks for every file already in the folder.

A run that fails reports itself and raises; the queue redelivers it after
the visibility timeout and dead-letters it after five receives.

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

Two tables on `api-kaianolevine-com`'s Railway Postgres:

**`wcs_transcripts`**
- `id` — UUID primary key
- `raw_text` — full transcript text (retained for future RAG/embeddings)
- `source_type` — inferred from filename
- `source_filename` — original Drive filename
- `drive_file_id` — Google Drive file ID
- `created_at`

**`wcs_notes`**
- `id` — UUID primary key
- `transcript_id` — FK to `wcs_transcripts`
- `title` — extracted by LLM
- `session_date` — extracted by LLM (ISO-8601)
- `session_type` — `private_lesson | class_taught | class_attended | workshop | coaching_session | other`
- `visibility` — `private | public` (defaults to `private`)
- `model` — LLM model used
- `provider` — LLM provider used
- `notes_json` — full structured notes as JSONB
- `created_at`

## Observability

| Layer | Mechanism |
|-------|-----------|
| L1 Liveness | `transcription-dlq-not-empty` CloudWatch alarm, with an email action |
| L2 Logs | `mini_app_polis` logger — structured JSON in production |
| L3 Exceptions | Sentry — SENTRY_DSN, initialised before any app logic |
| L4 Run history | One run report per run to Discord, under the queue message id |
