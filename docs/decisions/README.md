# Architecture Decision Records

This directory contains Architecture Decision Records (ADRs) for this
repository. ADRs document significant architectural decisions, the
context around them, and their consequences.

## Format

Each ADR is a markdown file named `ADR-NNNN-short-slug.md` where `NNNN`
is a zero-padded four-digit sequence number starting at `0001`. This
matches the ecosystem-standards DOC-005 specification. An ADR in another
repository is cited with that repository's name first, as in
`ecosystem-standards ADR-0009`.

Each ADR uses three sections: **Context** (what forces motivated the
decision), **Decision** (what change is being made), and
**Consequences** (what becomes easier or harder).

## Index

- [ADR-0001: Guard folder scan and writes with a Prefect concurrency slot](./ADR-0001-prefect-concurrency-slot.md) — superseded by ADR-0007
- [ADR-0002: Skip duplicates by unique constraint, defer possible_duplicate_ prefix](./ADR-0002-duplicate-handling-strategy.md)
- [ADR-0003: Use LLM schema validity as the pipeline_evaluations signal](./ADR-0003-llm-output-as-pipeline-evaluation-signal.md) — amended: run reports go to `/v1/notify`
- [ADR-0004: Merge voicenotes-cog into transcription-cog](./ADR-0004-voicenotes-merge.md) — partly superseded by ADR-0007
- [ADR-0005: New extraction prompt and write path](./ADR-0005-new-extraction-prompt-and-write-path.md)
- [ADR-0006: Move voicenotes task capture from Todoist to Asana](./ADR-0006-todoist-to-asana.md) — write path superseded by ADR-0008; the move to Asana stands
- [ADR-0007: Run on Lambda behind SQS, one file per job, not Prefect on Railway](./ADR-0007-lambda-behind-sqs.md) — amended (FIFO queue, three receives, 660 s visibility)
- [ADR-0008: Hand voice-note tasks to the task manager instead of writing to the board](./ADR-0008-hand-tasks-to-the-task-manager.md)
