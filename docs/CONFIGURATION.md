# Configuration — transcription-cog

All configuration is via environment variables. Secrets are managed in
Doppler and reach the Lambda function through `infra/tf` — never through
`terraform.tfvars`. See `.env.example` for the full list of keys and
`infra/worker.tf` for what the function is given.

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

On Lambda, only what `infra/worker.tf` sets reaches the function. The
optional settings below other than the request timeouts are passed through
from Doppler when present (`var.tuning`); anything else takes its code
default.

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

- Project: `transcription-cog`
- Environments: `development`, `production`
- Reaches the Lambda function through `infra/tf`, which reads Doppler into
  Terraform's environment. Lambda caps the whole environment at 4 KB; `./tf`
  prints the total.

## Notes on secret rotation

The function reads its environment at cold start. After rotating a secret
in Doppler, re-apply with `./tf plan -out tfplan && ./tf apply tfplan` from
`infra/`; the next invocation picks it up.
