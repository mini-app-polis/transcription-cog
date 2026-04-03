"""LLM prompt construction for transcript → notes conversion.

Kept as a pure function with no side effects so it is trivially testable.
"""

from __future__ import annotations

_KNOWN_SECTIONS = """\
  title                  - Short descriptive title for the session
  date                   - ISO-8601 date (YYYY-MM-DD) from transcript or filename
  session_type           - 'private_lesson' | 'class_taught' | 'class_attended' | 'workshop' | 'coaching_session' | 'other'
  participants           - List of objects with label, role, name (optional)
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

CORE RULE — CONTENT DRIVES STRUCTURE:
Only include a section in your output if the transcript genuinely contains that \
type of content. Omit it entirely rather than populating it with thin or invented content.

SESSION TYPE GUIDANCE:
  private_lesson  — the speaker is a student receiving 1-on-1 instruction
  class_taught    — the speaker is the instructor teaching a group class
  class_attended  — the speaker is a student in a group class taught by someone else
  workshop        — a convention, event, or intensive session
  coaching_session — performance or competition coaching

KNOWN SECTIONS (include only those present in this transcript):
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
- The source_filename may help infer date and session type.
- Output ONLY valid JSON. No markdown fences, no commentary outside the JSON.\
"""


def build_messages(
    transcript_text: str,
    source_filename: str = "",
) -> list[dict[str, str]]:
    """Return a provider-neutral message list for transcript → notes conversion.

    Args:
        transcript_text:  Raw transcript content.
        source_filename:  Original filename — helps the model infer date/session type.

    Returns:
        List of role/content dicts compatible with mini_app_polis.llm.LLMMessage.
    """
    system = _SYSTEM_TEMPLATE.format(known_sections=_KNOWN_SECTIONS)
    filename_line = f"Source filename: {source_filename}\n\n" if source_filename else ""
    user = f"{filename_line}Transcript begins below:\n\n{transcript_text}"

    return [
        {"role": "system", "content": system},
        {"role": "user", "content": user},
    ]
