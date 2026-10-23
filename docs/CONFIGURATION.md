# Configuration — transcription-cog

All configuration is via environment variables. Every variable, what it
does, its default and whether it is required, is in
[`.env.example`](../.env.example). This page holds what that file cannot.

Secrets are managed in Doppler, synced to SSM Parameter Store, and loaded by
the Lambda worker at cold start and again at every invocation — they never
pass through Terraform. Which names the function loads, required and
optional, is declared in mini-app-polis/infra; an optional setting it does
not load takes its code default.

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
