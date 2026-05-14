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
        ▼ (Prefect flow run: file_id, file_name, mime_type)
transcription-cog  ◄─── THIS COG
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

1. **Read transcript** — fetch raw text from Drive (Google Doc or .txt)
2. **Guard: length** — skip if below MIN_TRANSCRIPT_CHARS (default 200)
3. **Store transcript** — POST raw text to `/v1/wcs/transcripts`, get `transcript_id`
4. **Call LLM** — send transcript through structured prompt, get validated JSON notes
5. **Store notes** — POST structured notes to `/v1/wcs/notes`, get `note_id`
6. **Archive** — move original Drive file to processed folder

## Trigger

`watcher-cog` detects new files in `NOTES_INPUT_FOLDER_ID` and fires a Prefect
flow run with:
- `file_id` — Google Drive file ID
- `file_name` — original filename (used for source inference and LLM context)
- `mime_type` — MIME type of the file

During development the flow can be triggered manually from the Prefect UI
or via `uv run python -m transcription_cog.flow` with env vars set.

## watcher-cog configuration

A `WatcherConfig` entry must be added to `watcher-cog` pointing to this flow.
This is tracked as a separate task on `watcher-cog`.

Required config:
- Input folder: `NOTES_INPUT_FOLDER_ID`
- Flow name: `process-transcript`
- Deployment name: `transcription-cog`

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
| L1 Liveness | Healthchecks.io — HEALTHCHECKS_URL pinged on startup |
| L2 Logs | `mini_app_polis` logger — structured JSON in production |
| L3 Exceptions | Sentry — SENTRY_DSN, initialised before any app logic |
| L4 Orchestration | Prefect Cloud — run history, step logs, failure alerts |
