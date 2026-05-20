# ADR-005: New extraction prompt and write path for entity substrate

Date: 2026-05-19

## Status

Accepted

## Related

- `api-kaianolevine-com` ADR-0002 (WCS entity substrate)
- `api-kaianolevine-com` ADR-0003 (Versioned extractions and corrections)
- `api-kaianolevine-com` ADR-0004 (Rebuild WCS corpus from scratch)

## Context

`transcription-cog` extracts structured notes from WCS lesson transcripts via LLM and writes them to `api-kaianolevine-com` via `POST /v1/wcs/notes`. The current extraction prompt produces a flat `notes_json` shape — lists of `key_concepts`, `vocabulary_terms`, `patterns_and_sequences`, `drills`, `common_mistakes`, `student_observations`, `action_items`, `competition_notes`, `quotes`, `references`, `off_topic_notes`, `suggested_new_sections`.

The downstream API has committed (per `api-kaianolevine-com` ADR-0002) to a normalized entity substrate: canonical knowledge lives as `entities` (kinded as concept/technique/pattern/drill), `entity_relations`, `source_attributions`, `drill_purposes`, and `technique_requirements`. The current flat `notes_json` doesn't carry the entity kinds, the relationships, or the skill-layer signals (drill purposes, technique requirements) that the new substrate expects.

`transcription-cog` needs to change in two places:

1. **The extraction prompt** must ask the LLM for the entity-kinded structure the new substrate consumes — including relationships between entities and skill-layer signals.
2. **The write path** must POST to a new endpoint that accepts the new structure, not the flat `notes_json` shape.

## Decision

### New extraction prompt

The prompt is restructured to produce a single JSON document per transcript containing:

**Entity claims, kinded.** Instead of `key_concepts`, `vocabulary_terms`, `patterns_and_sequences`, `drills` as parallel flat lists, the prompt asks for `entities` — a single list where each entity carries an explicit `kind` (one of `concept`, `technique`, `pattern`, `drill`) plus its attribution prose.

Operational guidance in the prompt:
- **concept** — abstract idea, often compound, often informs how techniques are applied (e.g., "musicality", "connection", "settle", "leverage")
- **technique** — a tool to execute, named action a dancer performs (e.g., "anchor step", "bow-and-snap")
- **pattern** — a named figure in WCS movement vocabulary (e.g., "sugar push", "whip", "basket whip")
- **drill** — an exercise to practice a technique, with steps and a goal (e.g., "the paper drill", "10-second walk")

The prompt acknowledges that kind boundaries are sometimes fuzzy and instructs the LLM to pick the kind that best matches the source's framing. Downstream corrections handle mis-classifications (see ADR-0003).

**Entity relationships.** A new top-level `entity_relations` field captures cross-entity structure asserted by the source. Each relation has `from`, `to`, `relation_kind` (free string), and optional prose. The prompt provides examples of common relation kinds:
- `drill_trains_technique`
- `pattern_executes_via_technique`
- `pattern_variant_of`
- `concept_informs_technique`
- `concept_contains_concept`
- `technique_serves_pattern`

The relation_kind is free-string by design — see `api-kaianolevine-com` ADR-0002. The LLM is encouraged to use the documented kinds but may emit others when the relationship doesn't fit; the API logs unknown kinds for review.

**Drill purposes.** For each drill, the prompt asks for the *purposes* — what skills the drill develops. Each purpose is a skill-shaped phrase ("smooth weight transfer at controlled tempo"). A drill can have multiple purposes; the prompt explicitly notes this. The purpose is distinct from the drill's name (which may be arbitrary, e.g., "the paper drill").

**Technique requirements.** For each technique, the prompt asks for the *requirements* — what skills the technique requires to execute. Each requirement is a skill-shaped phrase, parallel in shape to drill purposes. This populates the `technique_requirements` table downstream.

**Entity definitions.** When a source explicitly defines a vocabulary term, the prompt produces a `definition` claim attached to the relevant entity. This is distinct from attribution prose — a definition is "this is what this word means" while attribution prose is "this is how this teacher framed it."

**Common mistakes.** Preserved as attribution claims with `kind='mistake'` plus structured `mistake_text` and `correction_text` fields, attached to the entity the mistake is about.

**References to people.** The `references` field continues to capture named individuals (instructors, dancers, judges) mentioned but not teaching. The prompt's existing constraints (individual people only, not events/schools/orgs; emit separately when two people are mentioned together) are preserved.

**Sections deliberately kept on the extraction but not promoted to canonical entities:**
- `student_observations` — per-student feedback. Preserved on `source_extractions.raw_output` for future use by an assessment layer (per `api-kaianolevine-com` ADR-0002 deferred work). Not extracted into Layer 2.
- `action_items` — per-session homework. Preserved on the extraction; not extracted into Layer 2.
- `off_topic_notes` — preserved but unused canonically.
- `suggested_new_sections` — preserved; surfaces in the API's evaluation findings as schema-evolution signal.
- `quotes` — preserved on the extraction; future polish-pass work may surface them as attribution prose on instructor pages.

These are not dropped from extraction; they're dropped from the canonical layer's structural representation. The future assessment layer reads them from `raw_output`.

### New write path

`transcription-cog` writes to `POST /v1/wcs/sources` (new endpoint owned by the API). The payload shape:

```
{
  "transcript_id": "...",
  "filename_metadata": { recording_date, instructors, students, organization, session_type, topic },
  "extraction": {
    "extractor_version": "...",         # semver of this version of the prompt + cog
    "extractor_model": "...",
    "extractor_provider": "...",
    "prompt_version": "...",            # tracked separately from extractor_version
    "raw_output": { ... }               # the full LLM output, unmodified
  }
}
```

The API:
- Creates the `sources` row from filename metadata (per existing behavior).
- Creates a `source_extractions` row with `is_active = true` and the raw output blob.
- Triggers the Composition Service for this source, which writes the canonical Layer 2 rows.

The cog does not perform entity resolution, slug generation, or alias matching. That's the API's job. The cog's job is to produce the LLM extraction and POST it.

The old `POST /v1/wcs/notes` endpoint and `POST /v1/wcs/transcripts` endpoint are deprecated and will be removed once the rebuild (per ADR-0004) completes.

### Schema validation

The cog continues to validate the LLM output against a JSON schema before POSTing. The schema is updated to match the new structure (entity claims with kinds, relationships, drill purposes, technique requirements).

The schema lives in `transcription_cog/schema.py` as `NOTES_SCHEMA` is replaced by `EXTRACTION_SCHEMA`. Existing `maxLength` constraints on names (concept name, term, pattern name, reference name) are preserved and tightened where the new prompt allows narrower bounds. The `references[].type` enum is preserved.

### Pipeline evaluation signal

Per `transcription-cog` ADR-003, the cog emits one `pipeline_evaluations` finding per flow run summarizing schema validity. This continues unchanged. The dimension and severity rules apply identically — a schema-invalid extraction is a WARN regardless of which schema it failed against.

### Migration

Per `api-kaianolevine-com` ADR-0004, the existing corpus is rebuilt from scratch. `transcription-cog`'s role in the rebuild:

1. Deploy with the new prompt and new write path (Phase 2 of the rebuild).
2. During the rebuild's Phase 3, a one-off backfill script (operating outside `transcription-cog`'s normal flow scope) re-processes every transcript in `NOTES_PROCESSED_FOLDER_ID` through the new pipeline. The script may invoke `transcription-cog`'s flow tasks directly, or call the new write endpoint after running extraction itself.

The cog's `_process_one` function is the natural call site for the backfill. Keeping it usable from outside the flow context (taking config/api/google clients as arguments) supports this.

## Consequences

### What this enables

- The downstream substrate gets the structured data it's designed to consume. No structural mismatch between extraction output and canonical schema.
- Re-extraction is opt-in per source (per `api-kaianolevine-com` ADR-0003). When the prompt is iterated, individual sources can be re-extracted without disrupting the rest of the corpus.
- The cog stays focused on its job (transcript → structured extraction → POST). Entity resolution, alias matching, attribution computation all live on the API side where they have access to global state.

### What this costs

- The new prompt is more demanding of the LLM. Extracting `entity_relations` and skill-layer signals requires more sophisticated reasoning than producing a flat `key_concepts` list. Expect more prompt iteration cycles, more careful test-set construction, and possibly higher per-extraction cost.
- Schema validation becomes more complex. The new schema has more shape to validate. Schema-invalid extractions will likely be more common in the early prompt-iteration period.
- Backwards compatibility is broken with the old write path. `wcs.kaianolevine.com/notes` (the notes UI) must either consume the new substrate via a compatibility endpoint or be updated to read from the new entity tables directly. Coordination required.

### Risks

- **LLM under-extraction of relationships.** The new prompt asks for relationships between entities, but the LLM may default to listing entities without relating them. Mitigated by prompt examples and by accepting that early-corpus relationships will be sparse — the corpus has gaps by design, fillable by the addition API.
- **Mis-classification of entity kind.** "Anchor step" extracted as concept in one source and technique in another. Mitigated by the correction layer; persistent mis-classifications are flagged for review via `pipeline_evaluations` findings.
- **Drift between the prompt's relation-kind examples and what the corpus actually wants.** The free-string `relation_kind` lets this evolve, but if the LLM produces wildly different strings for semantically identical relations, the corpus accumulates noise. Mitigated by periodic review of distinct relation_kind values; reconciliation by the operator via correction or merge.

## Alternatives considered

**Keep `notes_json` shape; let the API do entity inference at write time.** Rejected. The LLM is the right place to do entity classification because it has the full transcript context. Doing it at the API would mean re-parsing the LLM's natural-language output to recover what kind of entity each item was — adding a layer of reconstruction that the LLM can produce directly if asked.

**Multiple LLM calls per transcript (one for entities, one for relations, one for drill purposes).** Rejected. The transcript context is shared; splitting the call multiplies cost and risks inconsistency. One LLM call asked for everything is cleaner.

**Manual entity-kind tagging by the operator after extraction.** Rejected. The point of LLM extraction is to do this automatically. The correction layer exists for the cases where the LLM is wrong; it's not the primary path.

**Continue using `POST /v1/wcs/notes` with the new shape under the hood.** Rejected. The endpoint name should match what it does. A new substrate gets a new endpoint; the old endpoint is removed cleanly.
