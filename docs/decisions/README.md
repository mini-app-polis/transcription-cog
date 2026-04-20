# Architecture Decision Records

This directory contains Architecture Decision Records (ADRs) for this
repository. ADRs document significant architectural decisions, the
context around them, and their consequences.

## Format

Each ADR is a markdown file named `NNNN-title-in-kebab-case.md` where
`NNNN` is a zero-padded sequence number starting at `0001`.

## Template

```markdown
# NNNN. Title of the decision

Date: YYYY-MM-DD

## Status

Proposed | Accepted | Superseded by [NNNN](./NNNN-other.md)

## Context

What is the issue that we're seeing that is motivating this decision?

## Decision

What is the change that we're actually proposing or doing?

## Consequences

What becomes easier or more difficult to do because of this change?
```

## Index

- [0001. Guard folder scan and writes with a Prefect concurrency slot](./0001-prefect-concurrency-slot.md)
- [0002. Skip duplicates by unique constraint, defer possible_duplicate_ prefix](./0002-duplicate-handling-strategy.md)
- [0003. Use LLM schema validity as the pipeline_evaluations signal](./0003-llm-output-as-pipeline-evaluation-signal.md)
