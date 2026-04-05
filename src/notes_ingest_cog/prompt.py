"""LLM prompt construction for transcript → notes conversion.

Kept as a pure function with no side effects so it is trivially testable.
"""

from __future__ import annotations

from .filename_parser import ParsedFilename

_KNOWN_SECTIONS = """\
  title                  - Optional; only if the transcript clearly supplies one (do not invent)
  summary                - 2-4 sentence plain-English summary of the session
  key_concepts           - High-level principles and ideas discussed
  vocabulary_terms       - Dance/instructor-specific terms with working definitions
  drills                 - Practice exercises with intent and steps
  common_mistakes        - Observed errors and corrections
  patterns_and_sequences - Named patterns, move sequences, or combinations
  student_observations   - Instructor observations on THIS student (private/coaching only)
  action_items           - Concrete homework or takeaways for the student
  competition_notes      - Judging strategy, competition structure, or competition-specific advice
  quotes                 - Memorable instructor quotes (verbatim where possible)
  references             - Named instructors, dancers, systems, or external resources cited
  off_topic_notes        - Significant non-dance tangents worth preserving
  suggested_new_sections - Content that doesn't fit above; flagged for schema review\
"""

_SYSTEM_TEMPLATE = """\
You are a Dance Lesson Notes Compiler. Your job is to convert a raw transcript \
into structured JSON notes.

AUTHORITATIVE METADATA (from the filename — do NOT infer or override these):
  recording_date: {recording_date}
  session_type: {session_type}
  instructors: {instructors}
  students: {students}
  organization: {organization}
  topic (from filename, if any): {topic}

Use the metadata above as ground truth. Do not output date, session type, \
participants, or organization fields — they are not part of your JSON schema. \
Focus only on transcript-derived content sections.

CORE RULE — HIGH CONFIDENCE OR BLANK:
Only include a section in your output if the transcript clearly supports it. \
Omit the section entirely rather than filling it with guesses, padding, or \
low-confidence invention.

KNOWN SECTIONS (include only those present with high confidence):
{known_sections}

SECTION-SPECIFIC GUIDANCE:

vocabulary_terms
  Capture any term the instructor defines, coins, or uses in a specialised way. \
High value for building a shared glossary across sessions.

drills
  Only include if the transcript describes an exercise with a clear intent and \
some method or steps. A passing mention of a concept is NOT a drill.

student_observations
  Direct instructor assessments of the student's current dancing — what is \
getting better, what still needs work. Do not include general teaching points here.

quotes
  Prefer verbatim instructor quotes that capture a principle memorably.

suggested_new_sections
  If you encounter content that does not fit any known section and seems like \
it could be a recurring, useful category, flag it here instead of inventing a new \
top-level key. Each item: suggested_name (snake_case), rationale, sample_content. \
If nothing qualifies, omit this field entirely.

GENERAL RULES:
- Do not invent facts. If something is unclear, use "(unclear in transcript)".
- Keep bullets concise — one clear idea per item.
- Output ONLY valid JSON. No markdown fences, no commentary outside the JSON.\
"""


def build_messages(
    transcript_text: str,
    parsed: ParsedFilename,
) -> list[dict[str, str]]:
    """Return a provider-neutral message list for transcript → notes conversion.

    Args:
        transcript_text: Raw transcript content.
        parsed: Filename-derived metadata (authoritative session context).

    Returns:
        List of role/content dicts compatible with mini_app_polis.llm.LLMMessage.
    """
    topic_display = parsed.topic if parsed.topic else "(none)"
    org_display = parsed.organization if parsed.organization else "(none)"
    system = _SYSTEM_TEMPLATE.format(
        known_sections=_KNOWN_SECTIONS,
        recording_date=parsed.recording_date,
        session_type=parsed.session_type,
        instructors=", ".join(parsed.instructors) if parsed.instructors else "(none)",
        students=", ".join(parsed.students) if parsed.students else "(none)",
        organization=org_display,
        topic=topic_display,
    )
    user = f"Transcript begins below:\n\n{transcript_text}"

    return [
        {"role": "system", "content": system},
        {"role": "user", "content": user},
    ]
