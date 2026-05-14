"""Pydantic model for the structured task extracted from a voice transcript.

The shape here is the contract between the Claude prompt
(``src/notes_ingest_cog/voicenotes/prompts/extract.md``) and the Todoist post step.
Changes to this model require corresponding changes to the prompt and
vice-versa.

Field model:

  - ``title`` — the imperative action, scannable in the inbox.
  - ``description`` — one paragraph weaving WHAT and WHY together. The
    body of the Todoist task description starts with this.
  - ``where`` / ``who`` / ``when`` — optional auxiliary fields. When
    present, the post-task formatter renders them as a metadata block
    below the description with human labels (Context / Peers /
    Timeline). Internal field names match the prompt's 5W framing so
    the prompt and the model speak the same vocabulary; the human
    labels live in one constant in the formatter so they can be
    changed without touching the model.
"""

from __future__ import annotations

from datetime import date

from pydantic import BaseModel, Field, field_validator


class ExtractedTask(BaseModel):
    """A voice note distilled into task fields.

    Returned by ``tasks/extract.py``, consumed by ``tasks/post_task.py``.
    """

    title: str = Field(
        ...,
        description=(
            "Short action-oriented title for the Todoist task. The prompt "
            "nudges Claude toward concise titles (shorter reads better in "
            "the inbox), but no hard cap is enforced here — over-strict "
            "limits caused parse failures with no upside, since Todoist "
            "itself accepts long titles. Empty/whitespace titles are "
            "handled defensively in ``compose_task_input`` (see "
            "``_EMPTY_TITLE_FALLBACK``)."
        ),
    )
    description: str = Field(
        ...,
        description=(
            "One paragraph that weaves WHAT is being done and WHY into a "
            "single readable body. Goes into the Todoist task description "
            "above the metadata block. No length limit — Todoist handles "
            "long descriptions fine."
        ),
    )
    where: str | None = Field(
        default=None,
        description=(
            "WHERE the work happens — system, location, file, repo. "
            "Rendered as ``Context · …`` in the metadata block. None if "
            "the transcript has no location-style detail."
        ),
    )
    who: str | None = Field(
        default=None,
        description=(
            "WHO is involved beyond the user themselves — peers, teams, "
            "stakeholders. Rendered as ``Peers · …`` in the metadata "
            "block. None if the note is just a personal reminder."
        ),
    )
    when: str | None = Field(
        default=None,
        description=(
            "WHEN the work is relevant — soft temporal phrases like "
            "'tonight', 'before the Friday review', 'this week'. "
            "Rendered as ``Timeline · …`` in the metadata block. None "
            "if the transcript has no time reference. Distinct from "
            "``due_date`` which is the resolvable ISO date."
        ),
    )
    due_date: date | None = Field(
        default=None,
        description=(
            "Optional ISO-8601 date. Set ONLY when the original transcript "
            "contained 'due' or 'by' followed by a resolvable date. The "
            "downstream task manager ignores this field — it's kept for "
            "telemetry and to surface drift if Claude starts inferring "
            "dates from soft language."
        ),
    )
    labels: list[str] = Field(
        default_factory=list,
        description=(
            "Suggested Todoist labels — open vocabulary, 0-3 short "
            "kebab-case strings that categorize the task. Claude picks "
            "freely; the validator normalizes (lowercase, kebab-case, "
            "dedupe) and caps at 3 to keep the inbox tidy. Empty when "
            "``needs_review=True`` so review tasks don't accumulate "
            "ad-hoc labels."
        ),
    )
    needs_review: bool = Field(
        default=False,
        description=(
            "True if the transcript was empty, gibberish, or had no "
            "actionable content. The Todoist task is still created (so "
            "nothing is silently dropped) but labeled 'review'."
        ),
    )

    @field_validator("title")
    @classmethod
    def _strip_trailing_punct(cls, v: str) -> str:
        """Trim leading/trailing whitespace and trailing punctuation.

        Task titles read better without trailing periods or commas
        ("Send report to Mark." → "Send report to Mark"). No length
        cap is enforced — see the field's docstring for the
        rationale.
        """
        return v.strip().rstrip(".!?,;: ").strip()

    @field_validator("description")
    @classmethod
    def _strip_description(cls, v: str) -> str:
        """Trim incidental whitespace from the description body."""
        return v.strip()

    @field_validator("where", "who", "when")
    @classmethod
    def _normalize_optional_str(cls, v: str | None) -> str | None:
        """Treat empty/whitespace-only optional fields as None.

        Claude sometimes returns ``""`` instead of ``null`` for absent
        Ws; we coerce so the formatter's "skip if None" rule kicks in
        uniformly.
        """
        if v is None:
            return None
        stripped = v.strip()
        return stripped or None

    @field_validator("labels", mode="before")
    @classmethod
    def _normalize_labels(cls, v: object) -> list[str]:
        """Normalize Claude's free-form labels into a tidy short list.

        Rules applied here so a single source of truth holds:
          - ``None`` → empty list (Claude can return null when no
            labels make sense).
          - Strip whitespace.
          - Lowercase.
          - Replace internal whitespace with a single hyphen so
            ``"follow up"`` becomes ``"follow-up"``.
          - Drop empty strings.
          - Dedupe while preserving order (first occurrence wins).
          - Cap at 3 — keeps inbox tidy and reins in label sprawl
            from a model that's left to its own devices on
            vocabulary.
        """
        if v is None:
            return []
        if not isinstance(v, list):
            return []
        seen: set[str] = set()
        out: list[str] = []
        for item in v:
            if not isinstance(item, str):
                continue
            normalized = "-".join(item.strip().lower().split())
            if not normalized or normalized in seen:
                continue
            seen.add(normalized)
            out.append(normalized)
            if len(out) >= 3:
                break
        return out
