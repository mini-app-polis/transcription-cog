# 0002. Skip duplicates by unique constraint, defer possible_duplicate_ prefix

Date: 2026-04-03

## Status

Accepted

## Context

When `watcher-cog` re-triggers on the same Drive file (e.g. the archive
move fails mid-run, or the user manually moves a file back into the input
folder), the flow would re-process the transcript — wasting an LLM call
and potentially creating a duplicate `wcs_notes` row.

`api-kaianolevine-com` enforces uniqueness via
`uq_wcs_transcripts_drive_file_id` on `wcs_transcripts`. The cog needs
to handle the resulting `IntegrityError` gracefully.

Ecosystem rule PY-013 recommends a stronger pattern: on duplicate
detection, rename the source file with a `possible_duplicate_` prefix
and move it to a review folder, so the raw input is preserved even if
something upstream is misconfigured.

## Decision

Phase 1 (current): when `create_transcript` raises with
`uq_wcs_transcripts_drive_file_id` or any `unique` constraint in the
exception string, log a warning, return a `skipped / already_processed`
result, and leave the file in place. The file will be retried naturally
on the next `watcher-cog` trigger if still present, or caught by the
next manual pass.

Phase 2 (deferred): implement `possible_duplicate_` prefix + move to a
dedicated review folder. This requires adding a review folder ID to
config, extending the Drive helper, and agreeing on a cleanup cadence.

## Consequences

- Phase 1 is safe: the unique constraint guarantees no duplicate
  transcripts regardless of cog-side logic. The skip-and-leave behavior
  is idempotent.
- Phase 1 is slightly opaque: duplicates stay in the input folder and
  rely on the log warning being noticed. Acceptable while Kaiano is the
  sole operator.
- Phase 2 would improve observability but has non-trivial config
  surface. Tracked as a rule deferral in `evaluator.yaml`.
