"""JSON schema for structured notes output.

Design principles:
  1. CONTENT-DRIVEN — every section is optional. The model only populates
     a section when content genuinely exists in the transcript.
  2. KNOWN SECTIONS — the properties below are the current recognised
     vocabulary. Adding a new section here promotes it from "suggested"
     to "canonical" and it will be rendered with its own heading.
  3. FEEDBACK LOOP — suggested_new_sections lets the model flag content
     that doesn't fit any known section. Review these periodically.
  4. STABILITY — additionalProperties: True means new model-invented keys
     won't cause validation failures.
"""

from __future__ import annotations

NOTES_SCHEMA: dict = {
    "type": "object",
    "additionalProperties": True,
    "properties": {
        # Always expected
        "title": {
            "type": "string",
            "description": "Short descriptive title for this session.",
        },
        "date": {
            "type": "string",
            "description": "ISO-8601 date of the session (YYYY-MM-DD).",
        },
        "session_type": {
            "type": "string",
            "description": (
                "One of: 'private_lesson', 'class_taught', 'class_attended', "
                "'workshop', 'coaching_session', or 'other'. Infer from context. "
                "Use 'class_taught' when the speaker is the instructor teaching a group. "
                "Use 'class_attended' when the speaker is a student in a group class."
            ),
        },
        "participants": {
            "type": "array",
            "description": "Named or role-labelled participants.",
            "items": {
                "type": "object",
                "additionalProperties": True,
                "properties": {
                    "label": {"type": "string"},
                    "role": {"type": "string"},
                    "name": {"type": ["string", "null"]},
                },
            },
        },
        "summary": {
            "type": "string",
            "description": "2-4 sentence plain-English summary of the session.",
        },
        # Core content sections
        "key_concepts": {
            "type": "array",
            "description": "High-level principles or ideas discussed.",
            "items": {
                "oneOf": [
                    {"type": "string"},
                    {
                        "type": "object",
                        "additionalProperties": True,
                        "properties": {
                            "concept": {"type": "string"},
                            "detail": {"type": "string"},
                        },
                    },
                ]
            },
        },
        "vocabulary_terms": {
            "type": "array",
            "description": "Dance-specific terms defined or meaningfully used.",
            "items": {
                "type": "object",
                "additionalProperties": True,
                "properties": {
                    "term": {"type": "string"},
                    "definition": {"type": "string"},
                },
            },
        },
        "drills": {
            "type": "array",
            "description": "Specific practice exercises with intent and method.",
            "items": {
                "type": "object",
                "additionalProperties": True,
                "properties": {
                    "name": {"type": "string"},
                    "goal": {"type": "string"},
                    "steps": {"type": "array", "items": {"type": "string"}},
                },
            },
        },
        "common_mistakes": {
            "type": "array",
            "description": "Errors observed or discussed, paired with the correction.",
            "items": {
                "type": "object",
                "additionalProperties": True,
                "properties": {
                    "mistake": {"type": "string"},
                    "correction": {"type": "string"},
                },
            },
        },
        "patterns_and_sequences": {
            "type": "array",
            "description": "Named patterns, move sequences, or combinations.",
            "items": {
                "oneOf": [
                    {"type": "string"},
                    {
                        "type": "object",
                        "additionalProperties": True,
                        "properties": {
                            "name": {"type": "string"},
                            "description": {"type": "string"},
                        },
                    },
                ],
            },
        },
        "student_observations": {
            "type": "array",
            "description": "Instructor observations about the specific student's dancing.",
            "items": {
                "oneOf": [
                    {"type": "string"},
                    {
                        "type": "object",
                        "additionalProperties": True,
                        "properties": {"observation": {"type": "string"}},
                    },
                ]
            },
        },
        "action_items": {
            "type": "array",
            "description": "Concrete takeaways or homework assigned.",
            "items": {
                "oneOf": [
                    {"type": "string"},
                    {
                        "type": "object",
                        "additionalProperties": True,
                        "properties": {
                            "action": {"type": "string"},
                            "rationale": {"type": "string"},
                        },
                    },
                ]
            },
        },
        "competition_notes": {
            "type": "array",
            "description": "Strategy, judging insight, or competition-specific advice.",
            "items": {
                "oneOf": [
                    {"type": "string"},
                    {
                        "type": "object",
                        "additionalProperties": True,
                        "properties": {
                            "note": {"type": "string"},
                            "context": {"type": "string"},
                        },
                    },
                ]
            },
        },
        "quotes": {
            "type": "array",
            "description": "Memorable or particularly clear instructor quotes.",
            "items": {
                "oneOf": [
                    {"type": "string"},
                    {
                        "type": "object",
                        "additionalProperties": True,
                        "properties": {
                            "speaker": {"type": "string"},
                            "quote": {"type": "string"},
                            "context": {"type": "string"},
                        },
                    },
                ]
            },
        },
        "references": {
            "type": "array",
            "description": "Named instructors, dancers, systems, or resources cited.",
            "items": {
                "oneOf": [
                    {"type": "string"},
                    {
                        "type": "object",
                        "additionalProperties": True,
                        "properties": {
                            "name": {"type": "string"},
                            "type": {"type": "string"},
                            "context": {"type": "string"},
                        },
                    },
                ],
            },
        },
        "off_topic_notes": {
            "type": "array",
            "description": "Significant non-dance tangents worth preserving.",
            "items": {
                "oneOf": [
                    {"type": "string"},
                    {
                        "type": "object",
                        "additionalProperties": True,
                        "properties": {
                            "topic": {"type": "string"},
                            "summary": {"type": "string"},
                        },
                    },
                ],
            },
        },
        # Feedback loop
        "suggested_new_sections": {
            "type": "array",
            "description": (
                "Content that doesn't fit any known section. Flagged for schema review. "
                "Do NOT invent a new top-level key — put it here instead."
            ),
            "items": {
                "type": "object",
                "additionalProperties": True,
                "properties": {
                    "suggested_name": {"type": "string"},
                    "rationale": {"type": "string"},
                    "sample_content": {"type": "string"},
                },
            },
        },
    },
}
