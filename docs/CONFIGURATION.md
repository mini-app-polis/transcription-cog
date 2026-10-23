# Configuration — transcription-cog

All configuration is via environment variables. Secrets are managed in
Doppler, synced to SSM Parameter Store, and loaded by the Lambda worker at
cold start and again at every invocation — they never pass through
Terraform. See `.env.example` for the
full list of keys, and `cogs.tf` in mini-app-polis/infra for which ones the
function loads (required and optional).

## Required variables

| Variable | Description |
|----------|-------------|
| `GOOGLE_CREDENTIALS_JSON` | Google service account credentials (full JSON string) |
| `NOTES_INPUT_FOLDER_ID` | Google Drive folder ID to watch for new transcripts |
| `NOTES_PROCESSED_FOLDER_ID` | Google Drive folder ID to archive processed files into |
| `ANTHROPIC_API_KEY` | Anthropic API key (required if `LLM_PROVIDER=anthropic`) |
| `KAIANO_API_BASE_URL` / `KAIANO_API_BASE_URL_DEV` | Base URL of `api-kaianolevine-com`, read by the shared API client: production reads `KAIANO_API_BASE_URL`, every other environment `KAIANO_API_BASE_URL_DEV`, with no fallback between them |
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
| `MIN_NOTE_CHARS` | `20` | Minimum length of an authored note (`… > Note - Topic` or `… > Correction - Topic`); a one-sentence correction is a whole note |
| `SENTRY_DSN_COGS` | — | Sentry project DSN, for both pipelines. Error tracking disabled if absent |
| `LOGGING_LEVEL` | `INFO` | Log level: `DEBUG`, `INFO`, `WARN`, `ERROR`. Re-applied at every invocation; an unrecognised value is logged and ignored |

### Voice notes: bounds on one job

One job has to fit in one Lambda invocation, so everything a voice-note
job may do is capped by a setting. These are fields on the voicenotes
`Settings` (`src/transcription_cog/voicenotes/config.py`), which holds
the budget they add up to; like the other optional settings, Lambda
reads them only when `cogs.tf` lists them, and uses the default
otherwise.

| Variable | Default | Description |
|----------|---------|-------------|
| `CLEANUP_MAX_BUCKETS_PER_RUN` | `3` | Most expired date folders one retention sweep opens, dated ones oldest first, then undated legacy monthly ones |
| `CLEANUP_MAX_FILES_PER_RUN` | `5` | Most archived files one retention sweep tries to trash, oldest first. The sweep runs after every note, so what one leaves the next takes |
| `CLAUDE_RETRY_WAIT_MAX_SECONDS` | `10` | Longest wait before the Anthropic SDK's one retry of the extraction call, which otherwise waits as long as `Retry-After` asks |

## Session type taxonomy

`session_type` comes from the filename (`filename_parser.py`), not the LLM.
The right-hand side of ` > ` decides it:

| Value | Meaning |
|-------|---------|
| `private_lesson` | One or more person names |
| `group_class` | A known organization or event name |

## Visibility

Sources are stored with `visibility=private`. Promotion to `public` is done
manually via `wcs.kaianolevine.com` or directly via the API.

## Doppler project structure

- Project: `mini-app-polis-ecosystem`, configs `dev` and `prd`
- `prd` syncs to SSM Parameter Store under `/mini-app-polis/prd/`; the worker
  loads its listed names at cold start and at every invocation. Local runs
  use `doppler run`.

## Notes on secret rotation

The worker reads its secrets again at the start of every invocation, so
after rotating one in Doppler the next run uses it once the sync has updated
Parameter Store — no deploy or cold start. If Parameter Store cannot be
reached on a refresh, the values already loaded are kept; if the required
ones cannot be loaded at all, every record goes back to the queue.
`SENTRY_DSN_COGS` is the exception: it is read once, at cold start.
