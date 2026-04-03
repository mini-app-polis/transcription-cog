# Configuration — notes-ingest-cog

All configuration is via environment variables. Secrets are managed via
Doppler → Railway. See `.env.example` for the full list of required keys.

## Required variables

| Variable | Description |
|----------|-------------|
| `GOOGLE_CREDENTIALS_JSON` | Google service account credentials (full JSON string) |
| `NOTES_INPUT_FOLDER_ID` | Google Drive folder ID to watch for new transcripts |
| `NOTES_PROCESSED_FOLDER_ID` | Google Drive folder ID to archive processed files into |
| `ANTHROPIC_API_KEY` | Anthropic API key (required if `LLM_PROVIDER=anthropic`) |
| `KAIANO_API_BASE_URL` | Base URL of `api-kaianolevine-com` (e.g. `https://api.kaianolevine.com`) |
| `KAIANO_API_INTERNAL_KEY` | Per-caller internal API key for service-to-service auth |
| `PREFECT_API_KEY` | Prefect Cloud API key |
| `PREFECT_API_URL` | Prefect Cloud workspace URL |

## Optional variables

| Variable | Default | Description |
|----------|---------|-------------|
| `LLM_PROVIDER` | `anthropic` | LLM provider: `anthropic` or `openai` |
| `LLM_MODEL` | provider default | Model name. Defaults: `claude-sonnet-4-6` (Anthropic), `gpt-4.1-mini` (OpenAI) |
| `OPENAI_API_KEY` | — | Required only if `LLM_PROVIDER=openai` |
| `MIN_TRANSCRIPT_CHARS` | `200` | Minimum transcript length to process |
| `SENTRY_DSN` | — | Sentry project DSN. Error tracking disabled if absent |
| `HEALTHCHECKS_URL` | — | Healthchecks.io ping URL. Liveness monitoring disabled if absent |
| `LOGGING_LEVEL` | `INFO` | Log level: `DEBUG`, `INFO`, `WARN`, `ERROR` |

## Session type taxonomy

The LLM infers `session_type` from the transcript. Valid values:

| Value | Meaning |
|-------|---------|
| `private_lesson` | 1-on-1 lesson where you are the student |
| `class_taught` | Group class where you are the instructor |
| `class_attended` | Group class where you are a student |
| `workshop` | Convention or event workshop |
| `coaching_session` | Performance or competition coaching |
| `other` | Anything else |

## Visibility

Notes default to `visibility=private`. Promotion to `public` is done
manually via `wcs.kaianolevine.com` or directly via the API.

## Doppler project structure

- Project: `notes-ingest-cog`
- Environments: `development`, `production`
- Syncs to: Railway (via Doppler → Railway native sync)

## Notes on secret rotation

`PREFECT_API_KEY` is used only at startup to connect to Prefect Cloud.
If rotated, restart the Railway service after updating Doppler.

Prefect Blocks (if used for flow-level secrets) are managed directly in
Prefect Cloud and are not synced from Doppler. Update manually on rotation.
