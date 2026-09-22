# transcription-cog

[![CI](https://github.com/mini-app-polis/transcription-cog/actions/workflows/ci.yml/badge.svg)](https://github.com/mini-app-polis/transcription-cog/actions/workflows/ci.yml)
[![Version](https://img.shields.io/github/v/tag/mini-app-polis/transcription-cog?label=version)](https://github.com/mini-app-polis/transcription-cog/releases)

Pipeline cog that hosts two related pipelines in **one Lambda function**
(`transcription-worker`) behind its own SQS queue (`transcription-jobs`).
One message is one file:

| Mode (`mode=…`)         | What it does                                                       | Source folder | Sink                                  |
| ----------------------- | ------------------------------------------------------------------ | ------------- | ------------------------------------- |
| `wcs-transcripts`       | WCS lesson transcripts → structured notes (LLM) → Postgres        | `NOTES_INPUT_FOLDER_ID`            | `api-kaianolevine-com` (`/v1/wcs/notes`) |
| `voicenotes`            | Voice notes → Whisper transcription → Claude extraction → Asana | `GOOGLE_DRIVE_VOICE_INBOX_FOLDER_ID` | Asana board intake column             |
| `voicenotes-cleanup`    | Manual operator sweep: trash archived audio older than retention  | `GOOGLE_DRIVE_VOICE_INBOX_FOLDER_ID/processed/` | (trashes in Drive)                  |

`watcher-cog` asks `api-kaianolevine-com` for one run per changed file —
`POST /v1/transcription/runs` with `{"mode", "drive_file_id"}` — and the
API enqueues it. The `voicenotes` mode runs the retention sweep after every
note, so `voicenotes-cleanup` (which names no file) is for an operator's
manual sweep only. One file per job rather than a folder sweep, because a
sweep outlives Lambda's 900-second ceiling; see
[ADR-007](docs/decisions/ADR-007-lambda-behind-sqs.md).

**WCS pipeline** — `wcs.kaianolevine.com` reads notes via `/v1/wcs/notes`.  
**Voicenotes pipeline** — output lands in the intake column of the operator's Asana board, assigned to them; original audio is archived to `voice-inbox/processed/YYYY-MM-DD/` (one folder per processing day; legacy `YYYY-MM/` monthly folders from before the daily-bucket change remain in place and are still drained by the cleanup flow).

See [docs/PIPELINE.md](docs/PIPELINE.md) for the full ecosystem flow diagram.

> **History:** The `voicenotes` and `voicenotes-cleanup` modes were merged
> in from the standalone [`voicenotes-cog`](https://github.com/mini-app-polis/voicenotes-cog)
> repository in May 2026 so the two pipelines share one deployment — then
> a Prefect router on Railway, since September 2026 one Lambda function. The
> legacy repo is deprecated. Sub-package code
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
git clone git@github.com:mini-app-polis/transcription-cog.git
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

### Run one file locally (development)

The flows are plain functions. With a populated `.env`:

```bash
uv run python -c "from transcription_cog.flow import process_transcript; print(process_transcript('<drive file id>'))"
uv run python -c "from transcription_cog.voicenotes.flows.ingest import voicenotes_ingest; print(voicenotes_ingest('<drive file id>'))"
```

Run reports are posted only in production, so a local run logs its report
rather than sending it.

---

## Environment variables

See [docs/CONFIGURATION.md](docs/CONFIGURATION.md) for full documentation
of all environment variables.

---

## Deployment

A Lambda function behind an SQS queue, both declared in [`infra/`](infra/README.md)
and applied with Terraform from a workstation. CI's `deploy` job ships the
code of each release through the shared `lambda-deploy.yml`; Terraform owns
the function's configuration. Secrets reach the function through
`infra/tf`, which reads them from Doppler.

### Prerequisites

1. **api-kaianolevine-com** — `POST /v1/transcription/runs` and migration
   033 (the `transcription-trigger` role).
2. **watcher-cog** — the `wcs-notes` and `voice-notes` watchers post once
   per changed file, with `mode=wcs-transcripts` and `mode=voicenotes`.
3. **infra/** applied, and the GitHub repository variables
   `AWS_DEPLOY_ROLE_ARN`, `AWS_REGION`, `AWS_FUNCTION_NAME` set from
   `terraform output`.

### Observability

1. **Liveness** — the `transcription-dlq-not-empty` alarm, with an email
   action (confirm the SNS subscription). There is no process to be alive
   between jobs; a job that fails every retry is what must reach a person.
2. **Logs** — the shared structured logger, in CloudWatch.
3. **Exceptions** — Sentry, from `SENTRY_DSN`. Voicenotes errors flow
   through the same DSN.
4. **Run history** — every run reports to Discord under its queue message
   id, including a run that failed.

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
  flow.py           process_transcript — one WCS transcript file
  worker.py         Lambda entrypoint: one queue message, one run
  voicenotes/       voice sticky-note sub-pipeline (merged from voicenotes-cog)
    _shared.py
    config.py       pydantic-settings Settings (voicenotes-specific env)
    clients/        whisper, claude, asana, drive
    flows/          ingest (one note), cleanup (retention sweep)
    models/         ExtractedTask
    prompts/        extract.md (Claude extraction prompt)
    tasks/          download, transcribe, extract, post_task, archive, emit_evaluation
    scripts/        setup_asana.py (one-time operator helper)

tests/
  conftest.py                env bootstrap
  transcription_cog/          WCS pipeline tests
  voicenotes/                voicenotes sub-pipeline tests
    conftest.py              client singletons, fixtures
    unit/
    integration/

docs/
  PIPELINE.md       ecosystem flow diagram and storage schema
  CONFIGURATION.md  every environment variable documented

infra/              queue, dead-letter queue, alarm, function, deploy role
```
