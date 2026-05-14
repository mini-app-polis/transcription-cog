# ADR-001: Guard folder scan and writes with a Prefect concurrency slot

Date: 2026-04-03

## Status

Accepted

## Context

`transcription-cog` is triggered by `watcher-cog` every time a new file lands
in the WCS notes input Drive folder. Under bursty conditions (multiple files
dropped close together, or a late-firing trigger while an earlier run is
still archiving), two concurrent flow runs can race:

- Both scan the same input folder and see the same file.
- Both call `task_store_transcript`, and one wins the
  `uq_wcs_transcripts_drive_file_id` unique constraint. The other pays for
  a wasted LLM call before failing.
- Archive timing becomes nondeterministic.

PIPE-004 in `ecosystem-standards` requires that any flow writing to shared
resources (Drive moves, API POST calls) guards execution with a concurrency
control.

## Decision

Wrap the entire body of the `process_transcript` flow in a runtime
concurrency slot:

```python
with concurrency("notes-ingest", occupy=1):
    ...
```

The slot `notes-ingest` is configured with `limit=1` in Prefect Cloud.
A second run triggered while the first is active blocks at the slot until
the first completes (including archiving).

The deployment also sets `concurrency_limit=1` as a belt-and-suspenders
guard at the deployment layer.

## Consequences

- Duplicate LLM calls on the same transcript are prevented at the flow
  level, not just the DB-constraint level.
- Bursty `watcher-cog` triggers serialize gracefully instead of racing.
- Throughput is capped at one flow run at a time — acceptable because
  processing is I/O-bound and Kaiano is the sole content source.
- If the slot is held indefinitely (e.g. a stuck run), all subsequent
  triggers block. Mitigation: Prefect slot leases time out by default.
