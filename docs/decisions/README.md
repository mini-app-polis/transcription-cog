# Architecture Decision Records

This directory contains Architecture Decision Records (ADRs) for this
repository. ADRs document significant architectural decisions, the
context around them, and their consequences.

## Format

Each ADR is a markdown file named `ADR-NNN-short-slug.md` where `NNN`
is a zero-padded three-digit sequence number starting at `001`. This
matches the ecosystem-standards DOC-005 specification.

Each ADR uses three sections: **Context** (what forces motivated the
decision), **Decision** (what change is being made), and
**Consequences** (what becomes easier or harder).

## Index

- [ADR-001: Guard folder scan and writes with a Prefect concurrency slot](./ADR-001-prefect-concurrency-slot.md)
- [ADR-002: Skip duplicates by unique constraint, defer possible_duplicate_ prefix](./ADR-002-duplicate-handling-strategy.md)
- [ADR-003: Use LLM schema validity as the pipeline_evaluations signal](./ADR-003-llm-output-as-pipeline-evaluation-signal.md)
- [ADR-004: Merge voicenotes-cog into transcription-cog](./ADR-004-voicenotes-merge.md)
- [ADR-005: New extraction prompt and write path](./ADR-005-new-extraction-prompt-and-write-path.md)
- [ADR-006: Move voicenotes task capture from Todoist to Asana](./ADR-006-todoist-to-asana.md)
