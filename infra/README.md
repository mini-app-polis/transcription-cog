# transcription-cog on AWS: one queue, one function

> **Secrets no longer pass through Terraform.** Doppler syncs them to SSM
> Parameter Store (`/mini-app-polis/prd/`) and the worker loads the names in
> `secrets.tf` at cold start. The `./tf` wrapper is gone; run `terraform`
> directly. Anything below about the wrapper, `TF_VAR_*` or secret variables
> describes the old arrangement. This directory moves to `mini-app-polis/infra`.

transcription-cog's queue, dead-letter queue, alarm, Lambda function and CI
deploy role. A copy of deejay-cog's `infra/` (itself a copy of
evaluator-cog's) with `name_prefix = "transcription"`.

**The runbook is evaluator-cog's** —
[evaluator-cog/infra/README.md](https://github.com/mini-app-polis/evaluator-cog/blob/main/infra/README.md):
order of operations, the cutover, rolling back, and what was verified once
for the whole account (Cloudflare does not challenge AWS egress, the DLQ
works, a zip is enough). Those are properties of the account, not of a cog,
and are not re-proven here. This file is what differs.

## What differs from deejay-cog

**One file per job, not a sweep.** deejay's job sweeps a folder. This
cog's cannot: the one Prefect run on record took about five minutes for a
transcript, so a sweep of a few outlives the 900-second ceiling. watcher-cog
names each changed file and the API enqueues one job per file; see
`docs/decisions/ADR-007-lambda-behind-sqs.md`.

**Nothing account-level.** `create_github_oidc_provider`,
`create_api_producer` and `create_account_budget` all default to **false**.
The OIDC provider, the API's producer user (whose `*-jobs` policy already
covers `transcription-jobs`) and the monthly budget live in evaluator-cog's
state. The worker's own role can receive from its queue and nothing else —
it cannot send to one, its own included.

**arm64, Python 3.11.** Measured against the lock: every compiled wheel has
an `aarch64-manylinux_2_17` build (glibc 2.17 at most, against Amazon
Linux 2's 2.26). No ffmpeg, no audio or ML library — Whisper is an API
call, and the recording is held in memory, never written to /tmp.
`ci.yml` passes the architecture, runtime and handler to the shared
`lambda-deploy.yml`, and they must match `worker.tf`.

**Size, measured.** ~171 MB unzipped, ~32 MB zipped, against 250 MB and the
deploy workflow's 47 MB guard. `googleapiclient` is 102 MB of it and is
used (Drive). Memory: ~170 MB peak with the largest recording Whisper
accepts; the function has 512.

**The environment is the tightest limit.** Lambda caps every key and value
together at 4 KB. This cog carries more than deejay — two LLM keys, Asana,
three folder ids — and the service-account JSON is most of it. `./tf`
prints the total and refuses to run past the cap. If it is over, the
credentials belong in SSM Parameter Store — a code change, not a Terraform
one.

**Only listed settings reach the function.** Railway received all of
Doppler; Lambda receives what `worker.tf` sets. Behaviour settings Doppler
may hold — `LLM_MODEL`, `CLAUDE_MODEL`, `ARCHIVE_RETENTION_DAYS` and the
rest of `var.tuning` — pass through when present and stay unset otherwise.
The LLM request timeouts do not: they are sized in code so a voice note fits
in one invocation.

**Concurrency is 1.** Two jobs for one file can be queued (watcher asks
again after a partial failure or a restart), and run at once both would
find the file in its inbox. The function reserves 1 (`reserved_concurrency`).
The mapping has no `scaling_config`: its floor is 2, and AWS refuses a
mapping maximum above the function's reservation. A
message arriving mid-run is throttled and redelivered, which is why
`max_receive_count` is 5.

## Order of operations for this cog

```bash
cp terraform.tfvars.example terraform.tfvars   # alert_email; no secrets
doppler setup --project <transcription-cog project> --config prd   # once per machine, in this directory
terraform init && terraform fmt -check && terraform validate
terraform plan -out tfplan    # expect: no budget, no OIDC provider, no producer user
terraform apply tfplan
```

**Always go through `./tf`.** It reads the secrets from Doppler into
Terraform's environment, fixes the Google key's line breaks, refuses to run
if `terraform.tfvars` would override any of them, checks the 4 KB
environment cap, never lets Terraform prompt, and prints nothing secret.

The secrets still end up in `terraform.tfstate` and `tfplan` in plaintext,
as any Terraform-managed secret does — both are gitignored.

1. **Apply.** The function is created holding a placeholder that cannot
   import, and the mapping is on. Until the first deploy, anything enqueued
   fails, retries and dead-letters — visible, not lost.
2. **Confirm the alert subscription.** AWS emails a link; an unconfirmed
   subscription delivers nothing.
3. **CI deploy path.** Three repository *variables* in GitHub (none are
   secret): `AWS_DEPLOY_ROLE_ARN`, `AWS_REGION`, `AWS_FUNCTION_NAME` from
   `terraform output`. The next release deploys.
4. **Probe**, as in evaluator-cog's runbook: a malformed record should come
   back in `batchItemFailures` with no `FunctionError`.
5. **Cut over, gap first.** Pause the Prefect deployment and scale the
   Railway service to zero, deploy api-kaianolevine-com (migration 033 and
   the route), *then* deploy watcher-cog's per-file trigger. Stopping first
   leaves a gap: files that land during it are in the inbox when watcher
   restarts, and watcher asks for them by name on startup. The other order
   runs both.
