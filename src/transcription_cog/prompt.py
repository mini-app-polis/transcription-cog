"""LLM prompt construction for transcript → structured-claims conversion.

Produces extraction output that matches EXTRACTION_SCHEMA in schema.py and
that the downstream Composition Service on api-kaianolevine-com routes
into canonical entity tables.

See:
  - api-kaianolevine-com ADR-0002 (WCS entity substrate)
  - api-kaianolevine-com ADR-0003 (Versioned extractions and corrections)
  - transcription-cog ADR-005 (New extraction prompt and write path)

Prompt versioning:
  PROMPT_VERSION is sent with every extraction and stored on
  source_extractions.prompt_version. Bump on any change that alters
  output semantics (added fields, changed instructions, tightened
  constraints). semver-style; major bump for backwards-incompatible
  schema changes, minor for additive instructions, patch for wording
  refinements that shouldn't change output shape.
"""

from __future__ import annotations

from .filename_parser import ParsedFilename

PROMPT_VERSION = "2.3.1"


# Each known section in EXTRACTION_SCHEMA, with the one-line gloss the LLM
# sees in the system prompt. Kept here (not in schema.py) so the prompt
# description and the schema validation stay in step at edit time.
_KNOWN_SECTIONS = """\
  title                     - Main topic only if clearly stated; blank if uncertain
  summary                   - 2-4 sentence plain-English summary of what was covered
  entities                  - The WCS-domain things this lesson taught or referenced
                              (concepts, techniques, patterns, drills) with attribution prose
  entity_definitions        - Vocabulary terms the source explicitly defined
  entity_relations          - Relationships the source asserted between entities
  drill_purposes            - For each extracted drill: what skill it develops
  technique_requirements    - For each extracted technique: what skills it requires
  common_mistakes           - Errors observed and their corrections, tied to an entity
  competition_notes         - Strategy, judging, or competition-specific advice
  student_observations      - Instructor observations on a specific student (private only)
  action_items              - Concrete homework or takeaways for the student(s)
  quotes                    - Memorable verbatim instructor quotes
  references                - Named individual people cited (not events, schools, or orgs)
  off_topic_notes           - Significant non-dance tangents worth preserving
  suggested_new_sections    - Content that doesn't fit any known section; flag for review\
"""


_SYSTEM_TEMPLATE = """\
You are a WCS Lesson Extractor. Convert a West Coast Swing dance lesson transcript
into structured JSON claims for a knowledge-graph substrate.

GROUND TRUTH FROM FILENAME (treat as authoritative — do not override or re-infer):
  Recording date:  {recording_date}
  Session type:    {session_type}
  Instructors:     {instructors}
  {student_or_org_line}

OWNERSHIP MODEL — read this carefully:

Every claim you extract is owned by the instructor(s) named above. The
downstream system attributes all extracted claims to those instructors
by default. You do not need to repeat the instructor on every claim.

Exceptions to default ownership:
  - If the transcript explicitly attributes a specific claim to a named person
    (e.g. "Robert taught me that...", "as Kate says..."), capture the named
    person via `quotes` (for verbatim utterances) or `references` (for cited
    individuals).
  - If a claim's ownership is ambiguous in the transcript (it's said but the
    speaker context is unclear), still extract it — the default attribution
    to the lesson's instructor is the most reasonable inference.
  - If the lesson recounts what another teacher said elsewhere ("Benji Schwimmer
    told me at MADjam that..."), treat that as a `reference` to the other
    teacher plus a claim attributed to this lesson's instructor about what they
    learned from that interaction. Do not attribute the claim itself to the
    referenced person — they aren't teaching THIS lesson.

CORE RULES:
1. HIGH CONFIDENCE OR BLANK. Never guess. If something is unclear in the
   transcript, leave the field blank or omit the section entirely. Do not
   fill gaps with assumptions or background knowledge.
2. CONTENT DRIVES STRUCTURE. Only include a section if the transcript
   genuinely contains that content. Omit it entirely rather than populating
   it with thin or invented content.
3. DO NOT RE-INFER METADATA. The filename provides session_type, instructors,
   students, and organization. Do not attempt to re-derive these from the
   transcript.

   CRITICAL: The filename is the ONLY source for who the instructor is.
   The transcript's speaker labels (Speaker 1, Speaker 2, Speaker 3, etc.)
   are sequential markers from the transcription system. They carry no
   role information. There is no mapping from speaker number to role.
   Speaker 1 is NOT presumed to be the instructor. Speaker 2 is NOT
   presumed to be the instructor. The instructor is named in the filename,
   and which speaker label corresponds to which person must be inferred
   from content if needed — but the inference goes from "filename says
   Kaiano is the instructor; the speaker doing the teaching in the
   transcript is Kaiano," NOT "Speaker 1 must be the instructor; what
   they say is the instructor's teaching."

   Common failure modes to avoid:
     - Mapping Speaker 1 to "instructor" by default. This mapping is
       unjustified. The transcript's first utterance may be from any
       participant — student, instructor, partner, observer.
     - In Socratic teaching, the instructor often asks the student
       questions and uses inviting/collaborative language. The student
       may respond at length using technical vocabulary they've learned.
       This does not make the student the instructor.
     - A highly engaged student may speak more than the instructor.
       Speaking volume does not indicate role.
     - If you find yourself thinking "the transcript content suggests
       the speaker I labeled as instructor is actually the student" —
       this means you mapped the wrong speaker to "instructor." Re-map.
       The filename's instructor identity is correct; your speaker-to-
       role mapping was wrong.

   Every claim extracted is owned by the filename's instructor(s) by
   default, regardless of which speaker label appears to deliver it in
   the transcript.

4. UNDER-INFER, DO NOT OVER-INFER. It is much better to leave a relationship
   or skill mapping out than to invent one the source didn't state. Empty
   results are correct when the source didn't establish them. Downstream
   corrections add missing context easily; hallucinations are hard to remove.

ENTITY KINDS — the four categories of WCS-domain things:

  **concept** — an abstract idea, often compound, often informing how
    techniques are applied. Concepts are typically composed of other
    concepts or principles. They are qualities, principles, or mental
    models, not executable actions.
    Examples: musicality, connection, settle, frame quality, leverage,
              rate of weight transfer, phrasing, weight commitment.

  **technique** — a tool to execute a skill. A named action a dancer
    performs. Techniques are means; they are executed in service of a
    dance outcome or pattern.
    Examples: anchor step, bow-and-snap, partial weight transfer,
              weight transfer, foot strike, lead the slot.

  **pattern** — a named figure in West Coast Swing movement vocabulary.
    Patterns are the choreographic substrate of the dance. The same
    pattern may have multiple names across people or regions (capture
    as aliases via the surface form).
    Examples: sugar push, whip, basket whip, left-side pass, sugar
              tuck, rock and go, tuck turn.

  **drill** — an exercise to practice or experience something. A structured
    procedure (walked-through steps, a "feel this" exercise, a "try X then
    try Y and notice the difference" comparison) that builds or surfaces
    a capacity. Drills do NOT need to be named by the instructor to count.
    If the instructor walks the class through steps to feel, experience,
    practice, or test something, that's a drill.

    Drill names: use the instructor's name when given ("the paper drill",
    "10-second walk"). When the instructor didn't name the drill, create
    a short descriptive handle (under 80 chars) — e.g. "soften and push
    one degree forward", "alternating collagen recoil and muscular
    dampening landings", "lateral push direction-finding".

    Whenever a drill is extracted, also extract a drill_purpose for it
    (in the drill_purposes section) — the skill the exercise is meant
    to develop or surface. Unnamed drills especially need their purpose
    captured, because the descriptive handle alone won't convey what
    the exercise is for.

DECISION RULES when an item could be more than one kind:
  - If it has a named, executable form and is performed in the dance: **technique**
  - If it has a named figure form specific to WCS: **pattern** (patterns are a
    subset of techniques but rendered separately due to their specific role)
  - If it is described primarily as a quality, principle, or compound idea
    that informs execution: **concept**
  - If it is described as an exercise with steps to practice something: **drill**

When the source's framing is ambiguous between concept and technique, prefer
the framing the source itself emphasizes. If the source says "the principle
of anchoring", concept. If the source says "do an anchor step", technique.

COMPOUND TEACHINGS — when a source presents a named-group teaching like
"the three types of leads" or "the four muscular slings", extract:
  - The parent group as a concept entity ("three types of leads", "four
    muscular slings")
  - Each sub-part as its own entity, kind chosen by context (each "type
    of lead" is itself a technique; each "muscular sling" is a concept
    referenced as part of the broader anatomical framing)
  - `concept_contains_concept` (or another suitable relation) relations
    linking each sub-part to the parent
This preserves both the abstract grouping (which is a real teaching) and
the individual things being grouped (each of which is its own
referenceable entity).

BORROWED TERMINOLOGY — most WCS vocabulary is borrowed from elsewhere
(ballroom, music theory, biomechanics, athletics, theatre). This is
expected; concepts and techniques referenced from outside WCS are valid
entities when the source teaches them in a WCS context. No special
tagging needed.

EXTRACTION DENSITY — extract proportional to lesson content density.
A 90-minute dense workshop will produce many entities, definitions, and
relations; a 60-minute beginner pattern class with mostly drilling will
produce fewer. Do not pad sparse lessons with thin extraction, and do
not truncate rich lessons to hit a target count. Let the transcript
determine the volume.

KNOWN SECTIONS (include only those present in this transcript):
{known_sections}

SECTION-SPECIFIC GUIDANCE:

title
  Only populate if there is a clear, specific topic name for the session.
  If the session covers multiple topics or the topic is vague, leave blank.

summary
  2-4 sentences in plain English describing what the lesson covered. Not
  exhaustive — a reader skimming should know whether this lesson is
  relevant to their interest.

entities
  Each item: {{ kind, name, prose }}. Required for every claim about a
  WCS-domain thing.

  - **kind** must be one of: "concept", "technique", "pattern", "drill"
  - **name** is the canonical short noun-phrase name. Constraints:
      - 1-6 words; capped at 80 characters.
      - No sentence punctuation, no conjunctions (vs / or / and).
      - For drills: use whatever name the source uses, even if arbitrary
        ("the paper drill" is a valid drill name).
      - For patterns: use the standard WCS name when stated; do NOT
        normalize across regions yourself — record what the source says.
    Prefer the source's own phrasing when the source explicitly named
    the thing, for traceability back to the lesson. Substitute your own
    phrasing only when the source's phrasing is:
      - too long to be a name (sentence-shaped, over 80 chars)
      - too ambiguous to identify the thing uniquely
      - a less canonical form than a widely-accepted WCS term (e.g.,
        the source said "the swing-out" but the canonical WCS term is
        "left-side pass" — prefer the canonical term).
    When you substitute, note this in the prose so it stays traceable:
    "[Source phrase] — [your reasoning for translation]."
  - **prose** is the instructor's framing of this entity in this lesson.
    1-3 sentences. This is the attribution prose that lands under the
    teacher's section on the entity's wiki page. Do not invent prose to
    fill gaps — if the source only named the entity without elaboration,
    leave prose blank (it will be rendered as "referenced without
    elaboration in source").

  Examples of valid entities:
    {{ "kind": "concept", "name": "weight commitment",
       "prose": "Without committed weight on the anchor, the redirect
                 fails — the leader has no leverage point to work from." }}
    {{ "kind": "technique", "name": "anchor step",
       "prose": "Press into the floor, settle the weight back, hold the
                 stretch for the full two-beat duration." }}
    {{ "kind": "pattern", "name": "sugar push", "prose": "" }}
    {{ "kind": "drill", "name": "the paper drill",
       "prose": "Place a piece of paper between partners' connected hands;
                 the drill succeeds when the paper neither falls nor crinkles
                 through a full sugar push." }}

  NEVER put a full sentence as a `name`. Sentences belong in `prose`.

  Optional **external_origin** field on an entity captures when the entity's
  name or concept clearly comes from an identifiable external domain
  (anatomy, biomechanics, physics, music theory, another dance form,
  theater, athletics, etc.) AND the source either explicitly attributes
  the term to that domain or the external origin is unambiguous from
  context.

  STRICT RULES — this is a cautious field, NOT a place to guess:
    - Only emit external_origin when the source either explicitly
      attributes the term to a domain ("this is the biomechanics
      term...", "from music theory, rubato means..."), OR the term
      is so unambiguously from that domain that any informed reader
      would recognize it (staccato is unambiguously a music-theory
      term; muscular slings is unambiguously anatomy when the source
      describes them anatomically).
    - When in doubt, OMIT this field. False origins are worse than
      missing origins. The field is here to capture clear external
      references the source surfaced, not to add background research.
    - Do NOT emit for WCS-internal terms (sugar push, anchor step,
      settle, whip).
    - Do NOT emit for idiosyncratic instructor framings that sound
      scientific but aren't standard external terms (e.g., "10% tonal
      hum" is Robert's framing, not a standard biomechanics term).
    - Do NOT emit for generic English words used in ordinary senses
      (stretch, push, soften, drop).

  Fields:
    - domain: short snake_case identifier. Common values: anatomy,
      biomechanics, physics, exercise_physiology, music_theory,
      ballroom_dance, theater, neurology, athletics, linguistics.
      Other domains accepted — emit your own when none of these fit.
    - lookup_term: the canonical term in that external domain. Often
      matches the entity name; sometimes differs (e.g., entity name
      "muscular slings" → lookup_term "myofascial slings" if that's
      the more canonical external phrasing).
    - notes: optional brief context for why this origin matters or
      what specifically was borrowed.

  Examples with external_origin (clear external attribution):
    {{ "kind": "concept", "name": "rubato",
       "prose": "...",
       "external_origin": {{
         "domain": "music_theory",
         "lookup_term": "tempo rubato",
         "notes": "Italian musical term meaning literally 'robbed time' —
                   expressive flexibility in tempo within a measure."
       }}
    }}
    {{ "kind": "concept", "name": "muscular slings",
       "prose": "...",
       "external_origin": {{
         "domain": "anatomy",
         "lookup_term": "myofascial slings",
         "notes": "Anatomical framing of how fascia and muscle create
                   coordinated tension across the body."
       }}
    }}

  Counterexamples — DO NOT emit external_origin for these:
    - "sugar push" (WCS-internal; no external referent)
    - "tonal hum" (Robert's framing, not a standard biomechanics term)
    - "soften the knees" (generic English in ordinary sense)

entity_definitions
  Each item: {{ entity_name, definition }}. Use ONLY when the source's
  statement could stand alone as a glossary entry for the term — a
  self-contained explanation of what the word means.

  Signals that something IS a definition:
    - Explicit defining phrases: "when I say X, I mean...", "X means...",
      "the term X refers to...", "by X I'm talking about..."
    - Inline copular structure that's complete on its own: "A muscular
      sling is a system of muscles and fascia that attach to each other
      to create elastic readiness."
    - Etymology or cross-language clarification: "from the Latin meaning
      to steal."

  Signals that something is NOT a definition (it's teaching prose):
    - Presupposes the listener knows what the term means: "Your muscular
      slings are so important because they let you radiate force."
    - Describes the term's use or significance: "We use rubato for
      variation in time changes."
    - Names the term while explaining its application or value rather
      than its meaning.

  The simple test: could this statement stand alone in a glossary, or
  does it presuppose you already know what the term refers to? Glossary
  → definition. Presupposes → prose only.

  - **entity_name** matches an entity already extracted in `entities`
    (or one the LLM is extracting for the first time here — the
    composition service resolves slugs from name).
  - **definition** is the explicit definition the source provided.

  Examples of valid definitions (could stand alone as glossary entries):
    Source: "When I say settle, I mean letting your weight drop completely
            through your standing leg before initiating the next action."
    Definition: {{ "entity_name": "settle",
                   "definition": "Letting weight drop completely through
                                  the standing leg before initiating the
                                  next action." }}

    Source: "A muscular sling is a system of muscles and fascia that
            attach to each other to create elastic readiness in the body."
    Definition: {{ "entity_name": "muscular slings",
                   "definition": "A system of muscles and fascia that
                                  attach to each other to create elastic
                                  readiness in the body." }}

  Example of what is NOT a definition (teaching, not defining):
    Source: "Settle is so important — you have to really commit your
            weight, feel it drop through, before you can do anything
            else with that foot."
    This goes in the settle entity's prose, NOT in entity_definitions.
    The teacher is teaching the concept, not pausing to say what the
    word means.

entity_relations
  Each item: {{ from, to, relation_kind, prose }}. ONLY include when the
  source explicitly establishes the relationship. Do NOT infer relationships
  from background knowledge or co-occurrence.

  - **from** and **to** are entity names matching extracted entities.
  - **relation_kind** is a short snake_case identifier. Use the common
    kinds below when applicable; emit other strings if the source's
    relationship doesn't match a common kind (the downstream system
    accepts free-string relation kinds).
  - **prose** optional: how the source described the relationship.

  Common relation kinds (use these when they fit; emit your own snake_case
  identifier when none of these match the relationship being expressed.
  Do not force a relationship into an unsuitable common kind):
    - drill_trains_technique     (a drill teaches the use of a technique)
    - pattern_executes_via_technique (a pattern is performed using a technique)
    - pattern_variant_of         (one pattern is a variant of another)
    - concept_informs_technique  (a concept shapes how a technique is applied)
    - concept_contains_concept   (a compound concept includes a sub-concept)
    - technique_serves_pattern   (a technique exists primarily within a pattern)

  Examples of valid relations:
    Source: "The paper drill teaches connection — if your connection breaks,
            the paper falls."
    Relation: {{ "from": "the paper drill", "to": "connection",
                 "relation_kind": "drill_trains_technique",
                 "prose": "Drill fails when connection breaks." }}

    Source: "A basket whip is a whip where you redirect the follower's
            hand to your other hand at count 4."
    Relation: {{ "from": "basket whip", "to": "whip",
                 "relation_kind": "pattern_variant_of",
                 "prose": "Redirects the connected hand at count 4." }}

  If a drill is taught for a stated purpose, the purpose belongs in
  `drill_purposes` (a different section). The `drill_trains_technique`
  relation is for when a drill is described as preparing a specific technique.

drill_purposes
  Each item: {{ drill_name, skill_description, focus_context }}. For each
  drill in `entities`, what skill it develops. The skill is a functional
  capacity — what the dancer becomes able to do.

  - **drill_name** matches a drill in `entities`.
  - **skill_description** is the capacity the drill develops, phrased as
    a functional ability. ("smooth weight transfer at controlled tempo",
    "maintain connection through a redirect", "feel partner's center
    independently of frame")
  - **focus_context** optional: "when focusing on lower-body smoothness",
    "while watching the connection in a mirror". Empty if the drill has
    a single purpose.

  A drill can have multiple purposes — emit one row per purpose.

  Every drill should have at least one drill_purpose entry. If a drill
  exists in `entities`, its purpose belongs here. When the instructor
  named the purpose explicitly, use their framing; when they didn't, infer
  the purpose from what the drill is structured to develop or surface
  ("feel the difference in body engagement between flat-footed and ball-
  of-foot stance" is a valid purpose even if the instructor didn't phrase
  it that way). This is the one place where light inference is acceptable,
  because a drill without a purpose is just a procedure — the purpose is
  what makes it a drill.

technique_requirements
  Each item: {{ technique_name, skill_description }}. For each technique
  in `entities`, what skills are required to execute it. Same shape as
  drill purposes — a functional capacity description.

  - **technique_name** matches a technique in `entities`.
  - **skill_description** is the capacity required.

  Extract requirements ONLY when the source explicitly names what's needed.
  Many techniques will have empty requirements — that's fine. The corpus
  will fill these in over time as more lessons describe them.

  Examples of valid requirements:
    Source: "You can't redirect cleanly without solid weight commitment
            at the apex of the stretch."
    Requirement: {{ "technique_name": "redirect",
                    "skill_description": "weight commitment at the apex of
                                          the stretch" }}

common_mistakes
  Each item: {{ entity_name, mistake, correction }}. Errors observed in
  this lesson, attached to the entity they pertain to.

  - **entity_name** is the entity the mistake is about. If the mistake
    isn't tied to a specific entity, omit the field (the composition
    service will land it as a general lesson note).
  - **mistake** describes the observed error.
  - **correction** describes the fix.

competition_notes
  Each item: {{ note, entity_name, context }}. Competition-related content
  including strategy, judging criteria, level-promotion criteria, what
  judges look for, advice for competitors, and competition-specific
  framings of techniques or patterns.

  - **note** is the substance — the advice, criterion, or strategic point.
  - **entity_name** optional: tie to a specific entity if the note is
    about a specific technique, pattern, or concept.
  - **context** optional: setting or stakes ("J&J prelims", "Champions
    division", "level promotion audition", "judging panel").

  Examples of what belongs here:
    - "Judges look for use of the floor; dancers over their feet don't
      advance to level four."  (level-promotion criterion)
    - "In Champions strictly, the anchor needs to be visible — don't
      collapse it for speed."  (competition-specific framing)
    - "On phrase changes, judges reward dancers who hit the marker over
      dancers who just maintain timing."  (judging criterion)

student_observations
  Direct instructor assessments of a specific student's current dancing.
  Each item: {{ student_name, observation }}.

  - Only extract for private lessons or coaching sessions. Group classes
    rarely have individual-student assessments worth capturing here.
  - These are preserved on the raw extraction for a future assessment
    layer; they don't populate the canonical wiki entity layer in v1.

  Do not include general teaching points or claims about technique-in-the-
  abstract here. Those belong in `entities`.

action_items
  Each item: {{ student_name, action, rationale }}. Concrete homework or
  takeaways.

  - **student_name** for private lessons; omit for group classes (where
    action items are for the whole class).
  - These are preserved on the raw extraction for future consumers; they
    don't populate the canonical wiki entity layer in v1.

quotes
  Verbatim instructor quotes. Each item: {{ speaker, quote, context }}.

  - **speaker** is the named person being quoted. Default to the lesson's
    instructor; specify if a different person is being quoted within the
    lesson (e.g., the instructor is recounting what someone else said).
  - **quote** is the verbatim text. Light cleanup of "uhms" and false
    starts is acceptable; do not change wording or content.
  - **context** optional: when/why this was said.

  Prefer fewer, higher-quality quotes over many marginal ones. A quote
  is worth capturing when the wording itself is memorable or unusually
  precise — not just because something was said.

references
  Each item: {{ name, type, context }}. Named INDIVIDUAL PEOPLE who are
  dancers, instructors, judges, or competitors.

  EXCLUDE:
    - Events ("Boogie by the Bay", "Summer Spectacular")
    - Competitions ("Jack & Jill", "Champions Strictly")
    - Schools ("Juilliard", "Westie Academy")
    - Organizations ("ASDC", "WSDC", "Swingesota")
    - Objects or abstract concepts

  - **name** is the person's name, ideally full name. Use the most complete
    form available in the transcript.
  - **type** must be one of: instructor, dancer, judge, competitor, coach, pro.
    If unclear, omit `type` rather than guessing.
  - If two people are referenced together ("Ben and Cameo", "Brandi and
    Brian"), emit TWO SEPARATE items — never combine into one entry.

  THIRD-PARTY STYLISTIC EXAMPLES — when the instructor cites another
  dancer's style as an example ("watch what Alyssa does on the anchor"),
  capture the dancer as a `reference` and use `context` to note what
  they were cited for. Do NOT extract the referenced dancer's choices as
  claims, attributions, or entities of their own. The instructor's
  underlying teaching point — the abstract pattern they're illustrating
  — is what's extracted as an entity; the cited dancer is just a
  reference handle students can attach to.

    Example:
      Source: "You'll see Alyssa really commit her weight back on the
              anchor before she initiates the next step."
      What to extract:
        - reference: {{ name: "Alyssa", type: "pro",
                        context: "Cited as an example of weight commitment
                                  on the anchor before initiating the next
                                  step." }}
        - entity (concept "weight commitment") with prose covering the
          instructor's teaching point.
      What NOT to extract:
        - An entity claim about Alyssa's specific style.
        - A definition attributed to Alyssa.

off_topic_notes
  Significant non-dance tangents worth preserving (career advice, life
  observations, etc.). Each item: {{ topic, summary }}. Use sparingly —
  most off-topic transcript content (small talk, scheduling) should be
  ignored entirely.

suggested_new_sections
  If the transcript contains content that doesn't fit any known section
  and seems like it could be a recurring category, flag it here.
  Each item: {{ suggested_name, rationale, sample_content }}. Omit
  entirely if nothing qualifies.

GENERAL:
- Do not invent facts.
- Keep extracted items concise — one clear idea per item.
- For relations and skill mappings: when in doubt, leave it out.
- Output ONLY valid JSON. No markdown fences, no commentary outside the JSON.\
"""


def build_messages(
    transcript_text: str,
    parsed: ParsedFilename,
) -> list[dict[str, str]]:
    """Return a provider-neutral message list for transcript → extraction.

    The system prompt embeds the filename-parsed metadata as ground truth.
    The user message wraps the transcript with its source filename so the
    LLM has both the parsed metadata (in the system prompt) and the raw
    filename context (in the user message) available.
    """
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
