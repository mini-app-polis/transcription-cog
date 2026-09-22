variable "region" {
  description = <<-DESC
    Match the Railway fleet, which runs in US East, and the other cogs'
    stacks in the same account. The worker POSTs transcripts, sources and
    run reports to api-kaianolevine-com, so every round trip pays whatever
    distance separates the two.
  DESC
  type        = string
  default     = "us-east-1"
}

variable "name_prefix" {
  description = <<-DESC
    Prefix for every resource name. One prefix per cog.

    Load-bearing, not cosmetic: the queue is `<name_prefix>-jobs`, and the
    API derives the same name from the cog ("transcription") and its
    environment — `transcription-jobs` in production,
    `transcription-dev-jobs` elsewhere. A development stack is this
    directory applied with `transcription-dev`.
  DESC
  type        = string
  default     = "transcription"
}

variable "github_repo" {
  description = "owner/repo allowed to assume the deploy role via OIDC."
  type        = string
  default     = "mini-app-polis/transcription-cog"
}

# ── Budget ───────────────────────────────────────────────────────────────

variable "alert_email" {
  description = "Where this cog's DLQ alarm goes."
  type        = string
}

variable "create_account_budget" {
  description = <<-DESC
    Whether this state owns the account's monthly budget.

    False here. The budget is account-level and evaluator-cog's state owns
    it; a second one would send every overspend alert twice and look like
    two problems. Same shape as create_github_oidc_provider.
  DESC
  type        = bool
  default     = false
}

variable "budget_limit_usd" {
  description = "Monthly budget, when create_account_budget is true. Set low on purpose — this should never fire."
  type        = number
  default     = 10
}

# ── Worker sizing ────────────────────────────────────────────────────────

variable "worker_timeout_seconds" {
  description = <<-DESC
    Lambda's ceiling, and the reason a job is one file rather than a sweep
    of the folder. The queue's visibility timeout is derived from this
    rather than configured separately, so the two cannot drift apart.

    Measured before the move: one WCS transcript run on Prefect took about
    five minutes, the only run on record. A sweep of a few transcripts
    therefore outlives 900 s; one does not. Voice notes are budgeted in
    src/transcription_cog/voicenotes/config.py at 720 s worst case with
    every SDK retry spent. Neither has room to lower this; raise the
    per-request timeouts there only after re-doing that sum.

    The cost of leaving it high: a message that fails every time takes
    five visibility timeouts (~80 minutes) to reach the DLQ — which is
    also what now stands in for the Prefect extract task's 18-minute
    backoff through an Anthropic 529 spell.
  DESC
  type        = number
  default     = 900
}

variable "worker_memory_mb" {
  description = <<-DESC
    Measured, not guessed: imports come to ~120 MB, and holding a 25 MB
    recording — the largest Whisper accepts — three times over (the
    download, the upload buffer, the multipart body) peaks at ~170 MB.
    Nothing is written to /tmp. 512 is three times that peak. Lambda scales
    CPU with memory, and the work is mostly waiting on Drive, Whisper and
    Claude, so more would buy little; tune against the billed-duration and
    max-memory-used metrics.
  DESC
  type        = number
  default     = 512
}

variable "reserved_concurrency" {
  description = <<-DESC
    The serialisation knob, not a throttle. Two jobs for the same file can
    be queued — watcher asks again after a partial failure or a restart —
    and run at once they would both find the file still in its inbox and
    both process it: two transcripts, or two Asana lookups racing a
    create. Run one after the other, the second finds the file archived
    and does nothing. The Prefect deployment serialised the same way, with
    concurrency_limit = 1 and the notes-ingest slot.

    1 became settable when the account's concurrent-executions quota was
    raised to 1,000 (2026-09-21, per deejay-cog). If an apply refuses it,
    the quota increase has not landed: AWS refuses any reservation that
    leaves fewer than 100 unreserved executions.

    It is the only concurrency setting. The mapping's scaling_config cannot
    go below 2, and AWS refuses to create a mapping whose maximum exceeds the
    function's reservation, so there is none. The mapping's pollers can
    still hand over more messages than one invocation takes; the extra
    invocations are throttled. A throttled message goes back on the queue
    after the visibility timeout and the attempt counts toward
    max_receive_count, which is why that is 5 rather than 3: a burst must not
    dead-letter good work. Unlike deejay, bursts are normal here — a drop of
    three transcripts is three messages at once. A drop large enough that a
    message is throttled five times before its turn comes (roughly a dozen
    five-minute transcripts) can still dead-letter one, and the alarm says
    so; redrive it from the DLQ.
  DESC
  type        = number
  default     = 1
}

variable "max_receive_count" {
  description = "Deliveries before a message goes to the DLQ. Not automatic — without a redrive policy a poison message retries forever. 5, not 3, because reserved_concurrency = 1 means a burst can throttle a good message, and a throttled attempt still counts."
  type        = number
  default     = 5
}

variable "log_retention_days" {
  description = "CloudWatch Logs is inherited whether you want it or not; an explicit group means it does not retain forever by default."
  type        = number
  default     = 30
}

# ── Worker environment ───────────────────────────────────────────────────
#
# What the two pipelines read, and nothing else. Lambda caps the whole
# environment at 4 KB, and the Google service-account JSON is most of that
# — see README.md before adding anything. ./tf prints the total.

variable "kaiano_api_base_url" {
  description = "Base URL for api-kaianolevine-com."
  type        = string
  default     = "https://api.kaianolevine.com"
}

variable "transcription_cog_api_key" {
  description = "This cog's own named API key (CD-019), TRANSCRIPTION_COG_API_KEY. Unset or wrong means 401 on every transcript and every report."
  type        = string
  sensitive   = true

  validation {
    condition     = length(var.transcription_cog_api_key) >= 20 && !strcontains(var.transcription_cog_api_key, "...")
    error_message = "transcription_cog_api_key looks like the placeholder from terraform.tfvars.example."
  }
}

variable "google_credentials_json" {
  description = "Service-account JSON as a string, GOOGLE_CREDENTIALS_JSON. Drive for both pipelines."
  type        = string
  sensitive   = true

  # deejay's first apply shipped the example's placeholder, which the
  # Google client rejected at run time. Parsing here fails the plan instead.
  validation {
    condition = (
      can(jsondecode(var.google_credentials_json)) &&
      try(jsondecode(var.google_credentials_json).type, "") == "service_account" &&
      can(jsondecode(var.google_credentials_json).private_key)
    )
    error_message = "google_credentials_json must be a service-account JSON document (type = service_account, with a private_key)."
  }
}

variable "anthropic_api_key" {
  description = "ANTHROPIC_API_KEY. The WCS extraction and the voice-note extraction both use Claude."
  type        = string
  sensitive   = true
}

variable "openai_api_key" {
  description = "OPENAI_API_KEY, for Whisper."
  type        = string
  sensitive   = true
}

variable "asana_access_token" {
  description = "ASANA_ACCESS_TOKEN, the personal access token voice notes are posted with."
  type        = string
  sensitive   = true
}

variable "asana_workspace_id" {
  description = "ASANA_WORKSPACE_ID."
  type        = string
}

variable "asana_inbox_project_id" {
  description = "ASANA_INBOX_PROJECT_ID, the board voice notes land on."
  type        = string
}

variable "asana_inbox_section_id" {
  description = "ASANA_INBOX_SECTION_ID, the intake column. Optional: empty lands the task in the leftmost section."
  type        = string
  default     = ""
}

variable "notes_input_folder_id" {
  description = "NOTES_INPUT_FOLDER_ID, the WCS transcript inbox watcher-cog watches."
  type        = string
}

variable "notes_processed_folder_id" {
  description = "NOTES_PROCESSED_FOLDER_ID, where processed transcripts are archived."
  type        = string
}

variable "google_drive_voice_inbox_folder_id" {
  description = "GOOGLE_DRIVE_VOICE_INBOX_FOLDER_ID, voice-inbox/. The same value watcher-cog reads."
  type        = string
}

variable "tuning" {
  description = <<-DESC
    Non-secret settings that change behaviour, passed through when Doppler
    holds them: which model runs, how long archived audio is kept, how
    loud the logs are. Railway received all of Doppler; Lambda receives
    only what is listed here and above, so a setting missing from both
    would silently fall back to its code default in production. ./tf fills
    this from Doppler; only the names below are accepted.

    The LLM request timeouts are deliberately absent: they are sized so a
    voice note fits in one invocation, and an old Doppler value would
    break that budget. They live in code (voicenotes/config.py).
  DESC
  type    = map(string)
  default = {}

  validation {
    condition = alltrue([
      for k in keys(var.tuning) : contains([
        "LLM_PROVIDER",
        "LLM_MODEL",
        "MIN_TRANSCRIPT_CHARS",
        "LOGGING_LEVEL",
        "CLAUDE_MODEL",
        "WHISPER_MODEL",
        "ARCHIVE_RETENTION_DAYS",
      ], k)
    ])
    error_message = "tuning accepts only the settings listed in its description."
  }
}

variable "sentry_dsn" {
  description = "Sentry DSN for the worker, SENTRY_DSN."
  type        = string
  sensitive   = true
  default     = ""
}

variable "create_github_oidc_provider" {
  description = <<-DESC
    False when the account already has the GitHub OIDC provider — there can
    only be one per account, and a second `terraform apply` in a different
    cog's directory would otherwise fail on a resource that already exists.
    True for the first cog, false for every one after — so false here, by
    default rather than by remembering to pass it.
  DESC
  type        = bool
  default     = false
}

variable "create_api_producer" {
  description = <<-DESC
    Whether this state owns the API's sending identity.

    False here: the API's one producer user lives in evaluator-cog's state,
    and its `*-jobs` wildcard already covers transcription-jobs. There is
    one api-kaianolevine-com, so there is one IAM user for it, holding one
    access key.
  DESC
  type        = bool
  default     = false
}
