"""LLM prompt construction for transcript → notes conversion."""

from __future__ import annotations

from .filename_parser import ParsedFilename

_KNOWN_SECTIONS = """\
  title                  - Main topic only if clearly stated — blank if uncertain
  summary                - 2-4 sentence plain-English summary
  key_concepts           - High-level principles and ideas discussed
  vocabulary_terms       - Dance/instructor-specific terms with working definitions
  drills                 - Practice exercises with intent and steps
  common_mistakes        - Observed errors and corrections
  patterns_and_sequences - Named patterns, move sequences, or combinations
  student_observations   - Instructor observations on specific student (private only)
  action_items           - Concrete homework or takeaways
  competition_notes      - Judging strategy or competition-specific advice
  quotes                 - Memorable verbatim quotes
  references             - Named instructors, dancers, systems, or resources cited
  off_topic_notes        - Significant non-dance tangents worth preserving
  suggested_new_sections - Content that doesn't fit above; flagged for schema review\
"""

_SYSTEM_TEMPLATE = """\
You are a Dance Lesson Notes Compiler. Convert a raw transcript into structured JSON notes.

GROUND TRUTH FROM FILENAME (treat as authoritative — do not override or re-infer):
  Recording date:  {recording_date}
  Session type:    {session_type}
  Instructors:     {instructors}
  {student_or_org_line}

CORE RULES:
1. HIGH CONFIDENCE OR BLANK. Never guess. If something is unclear in the transcript,
   leave the field blank or omit the section entirely. Do not fill gaps with assumptions.
2. CONTENT DRIVES STRUCTURE. Only include a section if the transcript genuinely contains
   that content. Omit it entirely rather than populating it with thin or invented content.
3. DO NOT RE-INFER METADATA. The filename provides session_type, instructors, students,
   and organization. Do not attempt to re-derive these from the transcript.

KNOWN SECTIONS (include only those present in this transcript):
{known_sections}

SECTION-SPECIFIC GUIDANCE:

title
  Only populate if there is a clear, specific topic name for the session.
  If the session covers multiple topics or the topic is vague, leave blank.

key_concepts
  Each item is `{concept, detail}`.
  - `concept` is a SHORT NOUN-PHRASE name for the idea (2-8 words, no
    sentence punctuation). Examples of valid `concept` values: "anchor step",
    "frame quality", "directional intent", "bow-and-snap mechanic",
    "weight commitment".
  - `detail` is the prose elaboration — one or more sentences explaining
    how the teacher framed this concept in this lesson.
  NEVER put a full sentence into `concept`. Sentences belong in `detail`.
  If an idea cannot be reduced to a noun-phrase, omit it from `key_concepts`
  rather than emitting a sentence-shaped `concept`.

vocabulary_terms
  Capture any term the instructor defines, coins, or uses in a specialised way.
  Each item is `{term, definition}`.
  - `term` is the word or short phrase being defined (1-4 words, no punctuation).
  - `definition` is the prose definition.
  Same rule as `key_concepts`: NEVER put a sentence into `term`.

drills
  Only include if the transcript describes an exercise with clear intent and method.
  A passing mention of a concept is NOT a drill.

common_mistakes
  Each item is `{mistake, correction}`. `mistake` describes the observed error
  in a sentence; `correction` describes the fix. Both are full sentences here —
  unlike `key_concepts.concept`, no noun-phrase constraint applies.

patterns_and_sequences
  Each item is `{name, description}`. `name` is the established WCS pattern name
  (1-5 words: "sugar push", "left side pass", "basket whip"). `description` is
  the prose. NEVER put a sentence into `name`.

student_observations
  Direct instructor assessments of a specific student's current dancing.
  Do not include general teaching points. Only for private or coaching sessions.

quotes
  Prefer verbatim instructor quotes. Paraphrase only if the original is too fragmented.

references
  ONLY include named INDIVIDUAL PEOPLE who are dancers, instructors, judges, or
  competitors. EXCLUDE: events ("Boogie by the Bay"), competitions
  ("Jack & Jill"), schools ("Juilliard"), organizations ("ASDC"), objects,
  abstract concepts.
  - `name` must be a person's name, ideally full name. Use the most complete
    form available in the transcript. If only a first name is mentioned, use
    the first name.
  - If two people are referenced together ("Ben and Cameo"), emit TWO SEPARATE
    items — never combine into one entry.
  - `type` must be one of: instructor, dancer, judge, competitor, coach, pro.
    If the transcript doesn't make this clear, omit `type` rather than guessing.
  - If a name is mentioned but you cannot identify whether it's a person vs.
    event/org, OMIT it rather than guessing.

suggested_new_sections
  If you encounter content that doesn't fit any known section and seems like it
  could be a recurring category, flag it here. Each item: suggested_name (snake_case),
  rationale, sample_content. Omit entirely if nothing qualifies.

GENERAL:
- Do not invent facts.
- Keep bullets concise — one clear idea per item.
- Output ONLY valid JSON. No markdown fences, no commentary outside the JSON.\
"""


def build_messages(
    transcript_text: str,
    parsed: ParsedFilename,
) -> list[dict[str, str]]:
    """Return a provider-neutral message list for transcript → notes conversion."""
    instructors_str = ", ".join(parsed.instructors) if parsed.instructors else "unknown"

    if parsed.session_type == "private_lesson":
        student_or_org_line = (
            f"Students:        {', '.join(parsed.students)}"
            if parsed.students
            else "Students:        unknown"
        )
    else:
        student_or_org_line = (
            f"Organization:    {parsed.organization}"
            if parsed.organization
            else "Organization:    unknown"
        )

    system = _SYSTEM_TEMPLATE.format(
        recording_date=parsed.recording_date,
        session_type=parsed.session_type,
        instructors=instructors_str,
        student_or_org_line=student_or_org_line,
        known_sections=_KNOWN_SECTIONS,
    )

    filename_line = f"Source filename: {parsed.raw_filename}\n\n"
    user = f"{filename_line}Transcript begins below:\n\n{transcript_text}"

    return [
        {"role": "system", "content": system},
        {"role": "user", "content": user},
    ]
