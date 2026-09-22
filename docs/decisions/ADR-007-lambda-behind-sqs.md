# ADR-007: Run on Lambda behind SQS, one file per job, not Prefect on Railway

Date: 2026-09-21

## Status

Accepted

Supersedes [ADR-001](./ADR-001-prefect-concurrency-slot.md), and the
Prefect-deployment and Railway-service parts of
[ADR-004](./ADR-004-voicenotes-merge.md). The merge itself stands.

## Context

transcription-cog held a Railway container (4 CPU, 4 GB) open to poll
Prefect Cloud for runs that arrive when a transcript or a voice note lands
in Drive. The fleet is moving off Prefect onto one SQS queue and one Lambda
function per cog; the decision and its reasoning are evaluator-cog's
`docs/serverless-migration.md`, and evaluator-cog and deejay-cog are the
reference implementations. This is cog 3 of 4.

Whether the cog fits Lambda was measured before anything moved:

- **Package.** 171 MB unzipped, 32 MB zipped, against 250 MB and the
  deploy workflow's 47 MB guard, once Prefect goes (144 packages to 67).
  No ffmpeg and no audio or ML library: Whisper is an OpenAI API call. A
  zip is enough; no container image.
- **Native code.** Every compiled wheel has an `aarch64-manylinux_2_17`
  build (glibc 2.17 at most, against the runtime's 2.26) except
  `psycopg2-binary`, which nothing imported. It is dropped, and the
  function is arm64.
- **Memory and /tmp.** Imports come to ~120 MB. The largest recording
  Whisper accepts, 25 MB, held three times over peaks at ~170 MB. Nothing
  is written to /tmp.
- **Duration.** This is what did not fit. The one Prefect run on record
  took five minutes. Both flows swept their whole folder per run, so a
  drop of three transcripts outlives the 900-second ceiling, and the
  configured retry budgets — a 600-second Whisper task timeout, an
  18-minute backoff for Anthropic 529s — were never going to fit in one
  invocation.

What Prefect was actually doing here, checked against the source:

- **The trigger path.** watcher-cog called `create_flow_run` on the
  `transcription-cog` router. Replaced by watcher-cog POSTing
  `/v1/transcription/runs` once per file; the API enqueues onto
  `transcription-jobs`.
- **Task retries.** Live, unlike deejay's: the tasks raised. Every call
  but one goes through a client that retries itself — DriveFacade,
  KaianoApiClient, LLMClient, and the OpenAI and Anthropic SDKs. The one
  that does not is the shared Asana client, which leaves retrying to its
  caller; `post_task` now does it with tenacity. What outlasts a client's
  retry raises, and the queue redelivers the job.
- **Task timeouts.** Prefect cancelled a hung Whisper or Claude call. Now
  the per-request timeouts and SDK retry counts are set so one voice note
  fits: 720 s worst case, budgeted in `voicenotes/config.py`.
- **Concurrency.** `concurrency_limit=1` on the deployment and the
  `notes-ingest` / `voicenotes-cog` slots. Replaced by
  `reserved_concurrency = 1` on the function.
- **Failure hooks.** `on_failure` / `on_crashed`. Replaced by each flow
  sending its one report however it ends, before re-raising.
- **Run identity.** Prefect's flow-run id. Now the SQS message id, which is
  also what the API answered watcher with.

## Decision

`transcription_cog.worker.lambda_handler` is the entrypoint. One message is
one run:

    {"type": "transcription.run", "version": 1,
     "payload": {"mode": "wcs-transcripts" | "voicenotes", "drive_file_id": ...}}

or, for an operator's standalone retention sweep, `{"mode":
"voicenotes-cleanup"}` with no file.

**One file per job.** watcher already knows which files changed, so it
names them. The alternative — the cog sweeps the folder and asks the API
for one job per file it finds — was built and replaced before it shipped:
it put a second producer in the chain for information watcher holds.

A job first checks that its file is still in the inbox. Gone means an
earlier job for the same file archived it, and the run does nothing and
reports quietly. That check, the API's upsert on `drive_file_id` (a second
store replaces, never duplicates), the Asana external id and one job at a
time together make a repeated job harmless — and watcher repeats a request
after a partial failure or a restart.

A run is stopped 30 seconds before the function's timeout (SIGALRM in the
worker) and fails the ordinary way, reporting itself. Lambda kills a
timed-out invocation outright, so without that a run that ran long would
be retried with nobody told why.

A run that fails reports itself, naming the file and the step, then raises;
the worker names the message in `batchItemFailures` and sends nothing
further. The queue redelivers after the visibility timeout (960 s) and
dead-letters after five receives, which fires `transcription-dlq-not-empty`.

Prefect is removed from the dependencies, along with `main.py`,
`railway.json`, the voicenotes router flow and the batch-fatal classifier,
which existed to abort a Prefect batch. There was no `prefect.yaml`.

## Consequences

- No resident process. Nothing runs between recordings, and the Railway
  service goes.
- A failed file no longer rides along until the next trigger re-sweeps the
  folder. It is retried by the queue and, past five receives, sits in the
  dead-letter queue with an alarm; redriving it is the recovery.
- The long Anthropic backoff is now the queue's: five receives 16 minutes
  apart outlast the old 18-minute budget, at the cost of repeating the
  download and transcription for a voice note.
- A burst of files is several messages, serialised by the reserved
  concurrency. A throttled message costs a receive; a very large drop can
  dead-letter a good file, and the alarm says so.
- The WCS extraction goes through common-python-utils' Anthropic client,
  which does not pass `LLMConfig.timeout_s` to the SDK, so a hung request
  waits the SDK's 600 s default. The worker's deadline stops such a run
  before Lambda kills it, so it reports and is redelivered; the fix belongs
  in the shared library.
- mypy runs inside the shared `python-test.yml` stage through its
  `typecheck` input, since CD-026 allows no extra job name for it (TEST-012).
- Rolling back is a rewrite, not a restart, deliberately — keeping the old
  runner runnable is how two consumers end up running.
