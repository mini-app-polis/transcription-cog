# Configuration — transcription-cog

All configuration is via environment variables. Secrets are managed in
Doppler, synced to SSM Parameter Store, and loaded by the Lambda worker at
cold start — they never pass through Terraform. See `.env.example` for the
full list of keys, and `cogs.tf` in mini-app-polis/infra for which ones the
function loads (required and optional).

## Required variables

| Variable | Description |
|----------|-------------|
| `GOOGLE_CREDENTIALS_JSON` | Google service account credentials (full JSON string) |
| `NOTES_INPUT_FOLDER_ID` | Google Drive folder ID to watch for new transcripts |
| `NOTES_PROCESSED_FOLDER_ID` | Google Drive folder ID to archive processed files into |
| `ANTHROPIC_API_KEY` | Anthropic API key (required if `LLM_PROVIDER=anthropic`) |
| `KAIANO_API_BASE_URL` | Base URL of `api-kaianolevine-com` (e.g. `https://api.kaianolevine.com`) |
| `TRANSCRIPTION_COG_API_KEY` | This cog's own named API key. Sent directly as `Authorization: Bearer` on every internal request — no token exchange. The API matches it to identify this cog, so the audit trail records which cog acted. |

## Optional variables

On Lambda, only the names listed for transcription in mini-app-polis/infra
`cogs.tf` are loaded. The optional settings below other than the request
timeouts are loaded from Doppler when present (`ssm_optional_parameters`);
anything absent takes its code default.

| Variable | Default | Description |
|----------|---------|-------------|
| `LLM_PROVIDER` | `anthropic` | LLM provider: `anthropic` or `openai` |
| `LLM_MODEL` | provider default | Model name. Defaults: `claude-sonnet-4-6` (Anthropic), `gpt-4.1-mini` (OpenAI) |
| `OPENAI_API_KEY` | — | Whisper, for voice notes. Also the WCS extraction's key if `LLM_PROVIDER=openai` |
| `MIN_TRANSCRIPT_CHARS` | `200` | Minimum transcript length to process |
| `SENTRY_DSN` | — | Sentry project DSN, for both pipelines. Error tracking disabled if absent |
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

- Project: `mini-app-polis-ecosystem`, configs `dev` and `prd`
- `prd` syncs to SSM Parameter Store under `/mini-app-polis/prd/`; the worker
  loads its listed names at cold start. Local runs use `doppler run`.

## Notes on secret rotation

The worker loads its secrets at cold start. After rotating one in Doppler
the sync updates Parameter Store within moments, but warm instances keep
the old value until they are recycled — the next deploy, or any
configuration change, forces a cold start.
