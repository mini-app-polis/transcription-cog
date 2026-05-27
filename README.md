# transcription-cog

[![CI](https://github.com/mini-app-polis/notes-ingest-cog/actions/workflows/ci.yml/badge.svg)](https://github.com/mini-app-polis/notes-ingest-cog/actions/workflows/ci.yml)
[![Version](https://img.shields.io/github/v/tag/mini-app-polis/notes-ingest-cog?label=version)](https://github.com/mini-app-polis/notes-ingest-cog/releases)

Prefect pipeline cog that hosts two related pipelines under a **single
router-style deployment** (`notes-ingest-cog/notes-ingest-cog`):

| Mode (`mode=…`)         | What it does                                                       | Source folder | Sink                                  |
| ----------------------- | ------------------------------------------------------------------ | ------------- | ------------------------------------- |
| `wcs-transcripts`       | WCS lesson transcripts → structured notes (LLM) → Postgres        | `NOTES_INPUT_FOLDER_ID`            | `api-kaianolevine-com` (`/v1/wcs/notes`) |
| `voicenotes`            | Voice notes → Whisper transcription → Claude extraction → Todoist | `GOOGLE_DRIVE_VOICE_INBOX_FOLDER_ID` | Todoist Inbox project                 |
| `voicenotes-cleanup`    | Manual operator sweep: delete archived audio older than retention  | `GOOGLE_DRIVE_VOICE_INBOX_FOLDER_ID/processed/` | (deletes from Drive)                  |

Triggered by `watcher-cog` with `mode` set to one of the modes above (default
behavior in `voicenotes` already runs cleanup inline, so the explicit
cleanup mode is for operator-driven manual sweeps only). Mirrors
`deejay-cog`'s single-router-deployment pattern.

**WCS pipeline** — `wcs.kaianolevine.com` reads notes via `/v1/wcs/notes`.  
**Voicenotes pipeline** — output lands in the user's Todoist Inbox project; original audio is archived to `voice-inbox/processed/YYYY-MM-DD/` (one folder per processing day; legacy `YYYY-MM/` monthly folders from before the daily-bucket change remain in place and are still drained by the cleanup flow).

See [docs/PIPELINE.md](docs/PIPELINE.md) for the full ecosystem flow diagram.

> **History:** The `voicenotes` and `voicenotes-cleanup` modes were merged
> in from the standalone [`voicenotes-cog`](https://github.com/mini-app-polis/voicenotes-cog)
> repository in May 2026 so the two pipelines share one Prefect deployment
> and one Railway service. The legacy repo is deprecated. Sub-package code
> lives under `src/transcription_cog/voicenotes/`.

---

## Running locally

### Prerequisites

- Python 3.11+
- [uv](https://docs.astral.sh/uv/) installed
- A `.env` file populated from `.env.example`
- Google service account credentials with Drive access
- Anthropic or OpenAI API key

### Setup

```bash
git clone git@github.com:mini-app-polis/notes-ingest-cog.git
cd transcription-cog
uv sync --all-extras
uv run pre-commit install
uv run pre-commit run --all-files
cp .env.example .env
# populate .env with your values
```

### Run tests

```bash
uv run pytest
```

### Lint and format

```bash
uv run ruff check src tests
uv run ruff format src tests
```

### Trigger a flow run manually (development)

With Prefect Cloud credentials in your `.env`:

```bash
# Start serving the router deployment locally
uv run python -m transcription_cog.main

# Then trigger a specific mode via the Prefect UI's "Custom Run" dropdown,
# or from the CLI:
uv run prefect deployment run notes-ingest-cog/notes-ingest-cog \
  --param mode=wcs-transcripts

uv run prefect deployment run notes-ingest-cog/notes-ingest-cog \
  --param mode=voicenotes

uv run prefect deployment run notes-ingest-cog/notes-ingest-cog \
  --param mode=voicenotes-cleanup
```

---

## Environment variables

See [docs/CONFIGURATION.md](docs/CONFIGURATION.md) for full documentation
of all environment variables.

---

## Deployment

Deployed as a Railway worker service (no HTTP port). Secrets managed via
Doppler → Railway native sync.

### Prerequisites

These must be in place before the cog receives triggers:

1. **watcher-cog** — two `WatcherConfig` entries fire the single
   `notes-ingest-cog/notes-ingest-cog` deployment with the appropriate
   `mode` parameter:
   - `wcs-notes` watcher (`NOTES_INPUT_FOLDER_ID`) → `mode=wcs-transcripts`
   - `voicenotes` watcher (`GOOGLE_DRIVE_VOICE_INBOX_FOLDER_ID`) → `mode=voicenotes`
2. **Prefect Cloud** — the `notes-ingest-cog/notes-ingest-cog` router
   deployment is registered via `prefect.serve()` in `main.py` when
   the Railway worker starts.

### Post-deploy setup

After first deploy to Railway:

1. **Healthchecks.io** — create a new check (period: 1 min, grace: 5 min),
   set `HEALTHCHECKS_URL` in Doppler.
2. **Sentry** — create a Python project, set `SENTRY_DSN_NOTES_INGEST_COG`
   in Doppler. Voicenotes errors flow through the same DSN now that the
   two pipelines share a deployment.
3. **Prefect Cloud** — create a failure alert automation for the
   `notes-ingest-cog/notes-ingest-cog` deployment (covers both modes).

---

## Versioning

Versioning is managed by [semantic-release](https://semantic-release.gitbook.io/).
**Do not manually edit `pyproject.toml` version or `CHANGELOG.md`.**

Commit message format:
- `feat: description` — minor bump
- `fix: description` — patch bump
- `feat: description` + `BREAKING CHANGE: explanation` footer — major bump

---

## Project structure

```
src/transcription_cog/
  __init__.py       package init
  config.py         typed config from env vars (WCS pipeline)
  models.py         Pydantic models for all external data
  schema.py         LLM JSON schema for structured notes output
  prompt.py         LLM system + user message builder
  drive.py          Google Drive read + archive helpers (WCS pipeline)
  api_client.py     typed client for api-kaianolevine-com
  flow.py           Prefect @flow — process_transcript (WCS pipeline)
  main.py           service entry point: router flow + prefect.serve()
  voicenotes/       voice sticky-note sub-pipeline (merged from voicenotes-cog)
    _shared.py
    config.py       pydantic-settings Settings (voicenotes-specific env)
    clients/        whisper, claude, todoist, drive
    flows/          ingest, cleanup, router
    models/         ExtractedTask
    prompts/        extract.md (Claude extraction prompt)
    tasks/          download, transcribe, extract, post_task, archive, emit_evaluation
    scripts/        setup_todoist.py (one-time operator helper)

tests/
  conftest.py                shared Prefect test harness
  transcription_cog/          WCS pipeline tests
  voicenotes/                voicenotes sub-pipeline tests
    conftest.py              env bootstrap, singletons, no-op concurrency
    unit/
    integration/

docs/
  PIPELINE.md       ecosystem flow diagram and storage schema
  CONFIGURATION.md  every environment variable documented
```
