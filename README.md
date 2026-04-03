# notes-ingest-cog

[![CI](https://github.com/mini-app-polis/notes-ingest-cog/actions/workflows/ci.yml/badge.svg)](https://github.com/mini-app-polis/notes-ingest-cog/actions/workflows/ci.yml)
[![Version](https://img.shields.io/github/v/tag/mini-app-polis/notes-ingest-cog?label=version)](https://github.com/mini-app-polis/notes-ingest-cog/releases)

Prefect pipeline cog that processes WCS lesson transcripts into structured
notes stored in Postgres.

Triggered by `watcher-cog` when a new transcript file lands in the configured
Google Drive folder. Reads the transcript, calls an LLM to extract structured
notes, stores both the raw transcript and the structured notes via
`api-kaianolevine-com`, and archives the original file.

**Inputs:** `.txt` or Google Doc transcripts in a watched Drive folder  
**Outputs:** `wcs_transcripts` + `wcs_notes` rows in `api-kaianolevine-com` Postgres  
**Presentation:** `wcs.kaianolevine.com` reads notes via `/v1/wcs/notes`

See [docs/PIPELINE.md](docs/PIPELINE.md) for the full ecosystem flow diagram.

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
cd notes-ingest-cog
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
# Start serving the flow locally
uv run python -m notes_ingest_cog.main

# Then from the Prefect UI: trigger process-transcript with a file_id
# Or via Prefect CLI:
uv run prefect deployment run process-transcript/notes-ingest-cog \
  --param file_id=YOUR_DRIVE_FILE_ID \
  --param file_name=your_transcript.txt \
  --param mime_type=text/plain
```

---

## Environment variables

See [docs/CONFIGURATION.md](docs/CONFIGURATION.md) for full documentation
of all environment variables.

---

## Deployment

Deployed as a Railway worker service (no HTTP port). Secrets managed via
Doppler → Railway native sync.

### Post-deploy setup

After first deploy to Railway:

1. **Healthchecks.io** — create a new check (period: 1 min, grace: 5 min),
   set `HEALTHCHECKS_URL` in Doppler
2. **Sentry** — create a Python project, set `SENTRY_DSN` in Doppler
3. **Prefect Cloud** — verify the `process-transcript/notes-ingest-cog`
   deployment appears in the dashboard; create a failure alert automation
4. **watcher-cog** — add a `WatcherConfig` entry for `NOTES_INPUT_FOLDER_ID`
   pointing to the `process-transcript` flow (tracked as a separate task)

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
src/notes_ingest_cog/
  __init__.py       package init
  config.py         typed config from env vars
  models.py         Pydantic models for all external data
  schema.py         LLM JSON schema for structured notes output
  prompt.py         LLM system + user message builder
  drive.py          Google Drive read + archive helpers
  api_client.py     typed client for api-kaianolevine-com
  flow.py           Prefect @flow — main pipeline logic
  main.py           service entry point (Sentry, Healthchecks, prefect.serve)

tests/notes_ingest_cog/
  test_config.py    config normalization and failure paths
  test_prompt.py    prompt output shape
  test_drive.py     Drive helpers — normalization, failure, output shape
  test_flow.py      flow critical path — skip guards, happy path, output shape

docs/
  PIPELINE.md       ecosystem flow diagram and storage schema
  CONFIGURATION.md  every environment variable documented
```
