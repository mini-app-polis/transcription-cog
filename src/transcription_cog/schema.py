"""JSON schema for the new structured-claims extraction output.

Paired with prompt.py — the prompt produces this shape; the schema
validates it before the cog POSTs to api-kaianolevine-com.

See:
  - prompt.py for the human-readable extraction instructions
  - transcription-cog ADR-005 for the change rationale
  - api-kaianolevine-com ADR-0002 for the downstream substrate

The schema validates structure only — required fields, types, and enums.
Length preferences for names and skill descriptions are expressed in
prompt.py, not enforced here. Downstream normalization in the API's
composition service may trim or reshape verbose labels when needed.

Other design choices:
  - Required top-level keys are minimal (none). Every section is optional
    and omitted entirely when empty — the prompt enforces this.
  - `additionalProperties: True` on object items so the LLM can add
    fields without failing validation (additional fields are ignored
    downstream).
  - `relation_kind` is a free-string by design (api-kaianolevine-com
    ADR-0002 — relation kinds emerge from corpus before being typed).
"""

from __future__ import annotations

# ── Sub-schemas: reusable shapes ────────────────────────────────────────

_ENTITY_KIND_ENUM = ["concept", "technique", "pattern", "drill"]
_REFERENCE_TYPE_ENUM = [
    "instructor",
    "teacher",
    "dancer",
    "judge",
    "competitor",
    "coach",
    "pro",
]

# Sub-schema for the optional external_origin field on entities.
# Captures terms borrowed from identifiable external domains (anatomy,
# music theory, biomechanics, etc.) when the source either attributes
# the term explicitly or the origin is unambiguous from context.
# Conservative by design — emitted only for clear external references,
# never to add background research.
_EXTERNAL_ORIGIN_SCHEMA: dict = {
    "type": "object",
    "additionalProperties": True,
    "required": ["domain", "lookup_term"],
    "properties": {
        "domain": {
            "type": "string",
            "minLength": 1,
            "maxLength": 60,
            "description": (
                "snake_case identifier for the external field "
                "(anatomy, biomechanics, physics, music_theory, "
                "ballroom_dance, theater, etc.). Free-string by design; "
                "common domains will emerge from corpus."
            ),
        },
        "lookup_term": {
            "type": "string",
            "minLength": 1,
            "description": (
                "Canonical term in the external domain — what a reader "
                "would search for to learn more. Often matches entity "
                "name; sometimes differs."
            ),
        },
        "notes": {
            "type": "string",
            "description": (
                "Optional brief context for the origin — etymology, "
                "what specifically was borrowed, or why the origin "
                "matters."
            ),
        },
    },
}


_ENTITY_ITEM_SCHEMA: dict = {
    "type": "object",
    "additionalProperties": True,
    "required": ["kind", "name"],
    "properties": {
        "kind": {
            "type": "string",
            "enum": _ENTITY_KIND_ENUM,
            "description": "One of: concept, technique, pattern, drill",
        },
        "name": {
            "type": "string",
            "minLength": 1,
            "description": (
                "Canonical short noun-phrase name (1-6 words, no sentence "
                "punctuation, no conjunctions)."
            ),
        },
        "prose": {
            "type": "string",
            "description": (
                "Instructor's framing of this entity in this lesson. "
                "1-3 sentences. Empty when source named entity without "
                "elaboration."
            ),
        },
        "external_origin": _EXTERNAL_ORIGIN_SCHEMA,
    },
}


_ENTITY_DEFINITION_ITEM_SCHEMA: dict = {
    "type": "object",
    "additionalProperties": True,
    "required": ["entity_name", "definition"],
    "properties": {
        "entity_name": {
            "type": "string",
            "minLength": 1,
        },
        "definition": {
            "type": "string",
            "minLength": 1,
            "description": "Explicit definition the source provided.",
        },
    },
}


_ENTITY_RELATION_ITEM_SCHEMA: dict = {
    "type": "object",
    "additionalProperties": True,
    "required": ["from", "to", "relation_kind"],
    "properties": {
        "from": {
            "type": "string",
            "minLength": 1,
        },
        "to": {
            "type": "string",
            "minLength": 1,
        },
        "relation_kind": {
            "type": "string",
            "minLength": 1,
            "maxLength": 60,
            "description": (
                "Free-string relation kind (snake_case). Common kinds: "
                "drill_trains_technique, pattern_executes_via_technique, "
                "pattern_variant_of, concept_informs_technique, "
                "concept_contains_concept, technique_serves_pattern. "
                "Other strings accepted; downstream review surfaces "
                "uncommon kinds for potential standardization."
            ),
        },
        "prose": {
            "type": "string",
            "description": "Optional: how the source described the relationship.",
        },
    },
}


_DRILL_PURPOSE_ITEM_SCHEMA: dict = {
    "type": "object",
    "additionalProperties": True,
    "required": ["drill_name", "skill_description"],
    "properties": {
        "drill_name": {
            "type": "string",
            "minLength": 1,
        },
        "skill_description": {
            "type": "string",
            "minLength": 1,
            "description": (
                "Functional-capacity phrase — what the dancer becomes able "
                "to do. Examples: 'smooth weight transfer at controlled "
                "tempo', 'maintain connection through a redirect'."
            ),
        },
        "focus_context": {
            "type": "string",
            "description": ("Optional: 'when focusing on lower-body smoothness', etc."),
        },
    },
}


_TECHNIQUE_REQUIREMENT_ITEM_SCHEMA: dict = {
    "type": "object",
    "additionalProperties": True,
    "required": ["technique_name", "skill_description"],
    "properties": {
        "technique_name": {
            "type": "string",
            "minLength": 1,
        },
        "skill_description": {
            "type": "string",
            "minLength": 1,
            "description": (
                "Functional-capacity phrase — what the dancer needs to be "
                "able to do to execute this technique."
            ),
        },
    },
}


_COMMON_MISTAKE_ITEM_SCHEMA: dict = {
    "type": "object",
    "additionalProperties": True,
    "required": ["mistake", "correction"],
    "properties": {
        "entity_name": {
            "type": "string",
            "description": (
                "Optional: the entity this mistake is about. Omit if the "
                "mistake isn't tied to a specific entity."
            ),
        },
        "mistake": {
            "type": "string",
            "minLength": 1,
            "description": "Description of the observed error.",
        },
        "correction": {
            "type": "string",
            "minLength": 1,
            "description": "Description of the fix.",
        },
    },
}


_COMPETITION_NOTE_ITEM_SCHEMA: dict = {
    "type": "object",
    "additionalProperties": True,
    "required": ["note"],
    "properties": {
        "note": {
            "type": "string",
            "minLength": 1,
        },
        "entity_name": {
            "type": "string",
            "description": "Optional: tie to a specific entity.",
        },
        "context": {
            "type": "string",
            "description": "Optional: 'J&J prelims', 'Champions division'.",
        },
    },
}


_STUDENT_OBSERVATION_ITEM_SCHEMA: dict = {
    "type": "object",
    "additionalProperties": True,
    "required": ["observation"],
    "properties": {
        "student_name": {
            "type": "string",
            "description": "Student being observed. Optional for class settings.",
        },
        "observation": {
            "type": "string",
            "minLength": 1,
        },
    },
}


_ACTION_ITEM_ITEM_SCHEMA: dict = {
    "type": "object",
    "additionalProperties": True,
    "required": ["action"],
    "properties": {
        "student_name": {
            "type": "string",
            "description": "Student the action is for. Optional for class settings.",
        },
        "action": {
            "type": "string",
            "minLength": 1,
        },
        "rationale": {
            "type": "string",
            "description": "Why this action item — what it addresses.",
        },
    },
}


_QUOTE_ITEM_SCHEMA: dict = {
    "type": "object",
    "additionalProperties": True,
    "required": ["quote"],
    "properties": {
        "speaker": {
            "type": "string",
            "description": (
                "Named person being quoted. Defaults to the lesson's "
                "instructor when omitted."
            ),
        },
        "quote": {
            "type": "string",
            "minLength": 1,
        },
        "context": {
            "type": "string",
            "description": "Optional: when/why this was said.",
        },
    },
}


_REFERENCE_ITEM_SCHEMA: dict = {
    "type": "object",
    "additionalProperties": True,
    "required": ["name"],
    "properties": {
        "name": {
            "type": "string",
            "minLength": 1,
            "description": (
                "Individual person's name. Full name when available. "
                "Multiple people referenced together must be emitted as "
                "separate items."
            ),
        },
        "type": {
            "type": "string",
            "enum": _REFERENCE_TYPE_ENUM,
            "description": (
                "Person type. Omit if unclear from transcript — do not guess."
            ),
        },
        "context": {
            "type": "string",
            "description": "Optional: where/how the person was mentioned.",
        },
    },
}


_OFF_TOPIC_NOTE_ITEM_SCHEMA: dict = {
    "type": "object",
    "additionalProperties": True,
    "required": ["topic", "summary"],
    "properties": {
        "topic": {
            "type": "string",
            "minLength": 1,
        },
        "summary": {
            "type": "string",
            "minLength": 1,
        },
    },
}


_SUGGESTED_SECTION_ITEM_SCHEMA: dict = {
    "type": "object",
    "additionalProperties": True,
    "required": ["suggested_name", "rationale"],
    "properties": {
        "suggested_name": {
            "type": "string",
            "minLength": 1,
            "description": "snake_case name proposed for the new section.",
        },
        "rationale": {
            "type": "string",
            "minLength": 1,
        },
        "sample_content": {
            "type": "string",
        },
    },
}


# ── Top-level schema ────────────────────────────────────────────────────


EXTRACTION_SCHEMA: dict = {
    "type": "object",
    "additionalProperties": True,
    "properties": {
        "title": {
            "type": "string",
            "description": (
                "Main topic of the session if clearly stated. Blank when "
                "uncertain — do not invent."
            ),
        },
        "summary": {
            "type": "string",
            "description": "2-4 sentence plain-English summary.",
        },
        "entities": {
            "type": "array",
            "description": (
                "WCS-domain things this lesson taught or referenced "
                "(concepts, techniques, patterns, drills) with attribution "
                "prose."
            ),
            "items": _ENTITY_ITEM_SCHEMA,
        },
        "entity_definitions": {
            "type": "array",
            "description": "Vocabulary terms the source explicitly defined.",
            "items": _ENTITY_DEFINITION_ITEM_SCHEMA,
        },
        "entity_relations": {
            "type": "array",
            "description": "Relationships the source asserted between entities.",
            "items": _ENTITY_RELATION_ITEM_SCHEMA,
        },
        "drill_purposes": {
            "type": "array",
            "description": "What skill each extracted drill develops.",
            "items": _DRILL_PURPOSE_ITEM_SCHEMA,
        },
        "technique_requirements": {
            "type": "array",
            "description": "What skills each extracted technique requires.",
            "items": _TECHNIQUE_REQUIREMENT_ITEM_SCHEMA,
        },
        "common_mistakes": {
            "type": "array",
            "description": "Observed errors and their corrections.",
            "items": _COMMON_MISTAKE_ITEM_SCHEMA,
        },
        "competition_notes": {
            "type": "array",
            "description": "Strategy, judging, or competition-specific advice.",
            "items": _COMPETITION_NOTE_ITEM_SCHEMA,
        },
        "student_observations": {
            "type": "array",
            "description": (
                "Instructor observations on specific students. Preserved on "
                "raw extraction for future assessment-layer consumers; not "
                "promoted to canonical entities in v1."
            ),
            "items": _STUDENT_OBSERVATION_ITEM_SCHEMA,
        },
        "action_items": {
            "type": "array",
            "description": (
                "Concrete homework or takeaways. Preserved on raw extraction; "
                "not promoted to canonical entities in v1."
            ),
            "items": _ACTION_ITEM_ITEM_SCHEMA,
        },
        "quotes": {
            "type": "array",
            "description": "Memorable verbatim instructor quotes.",
            "items": _QUOTE_ITEM_SCHEMA,
        },
        "references": {
            "type": "array",
            "description": (
                "Named INDIVIDUAL PEOPLE cited (not events, schools, or orgs)."
            ),
            "items": _REFERENCE_ITEM_SCHEMA,
        },
        "off_topic_notes": {
            "type": "array",
            "description": "Significant non-dance tangents worth preserving.",
            "items": _OFF_TOPIC_NOTE_ITEM_SCHEMA,
        },
        "suggested_new_sections": {
            "type": "array",
            "description": (
                "Content that doesn't fit known sections and may be a "
                "recurring category. Surfaces in pipeline_evaluations for "
                "schema-evolution review."
            ),
            "items": _SUGGESTED_SECTION_ITEM_SCHEMA,
        },
    },
}
