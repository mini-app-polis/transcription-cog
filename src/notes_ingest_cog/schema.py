"""JSON schema for structured notes output."""

from __future__ import annotations

NOTES_SCHEMA: dict = {
    "type": "object",
    "additionalProperties": True,
    "properties": {
        "title": {
            "type": "string",
            "description": (
                "The main topic or name of this session, only if clearly stated. "
                "Leave blank if uncertain. Do not invent a title."
            ),
        },
        "summary": {
            "type": "string",
            "description": "2-4 sentence plain-English summary of what was covered.",
        },
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
            "description": "Dance-specific or instructor-specific terms that were defined or meaningfully used.",
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
            "description": "Specific practice exercises with clear intent and method.",
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
            "description": "Named patterns, move sequences, or combinations taught or referenced.",
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
            "description": "Instructor observations about a specific student. Private or coaching sessions only.",
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
            "description": "Concrete takeaways or homework for the student(s).",
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
            "description": "Memorable or particularly clear instructor quotes. Use the speaker's actual words.",
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
                ]
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
                ]
            },
        },
        "suggested_new_sections": {
            "type": "array",
            "description": (
                "Content that doesn't fit any known section. Flag here instead of "
                "inventing a new top-level key. Omit entirely if nothing qualifies."
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
