# ADR-006: Move voicenotes task capture from Todoist to Asana

**Status:** Accepted (September 2026)

## Context

The voicenotes pipeline has posted its extracted tasks to Todoist since
before the merge into this repo (ADR-004). Task tracking for the
ecosystem has since moved to an Asana board — one project with an
Ideas → Scoped → Refining → In progress → Review → Done column
process — and captured voice notes belong in the same place as
everything else being groomed. Two inboxes is one too many.

Two things about the Todoist implementation were load-bearing and do
not carry over:

1. **Idempotency by substring scan.** Todoist has no task-body search,
   so `find_task_by_drive_file_id` listed the project's active tasks
   and scanned each description for the Drive view URL embedded in the
   audio footer. The marker had to be prose in the body, and the check
   could only see *active* tasks: a note already triaged and checked
   off would be recreated on any replay of the file.
2. **Markdown bodies and free-string labels.** Todoist rendered
   `**Context**` and `[Listen](url)`, and a label was whatever string
   you sent.

Planned work — Discord messages arriving through
`api-kaianolevine-com` that create or update tasks — will need the same
Asana surface. That is what settles where the client lives, not this
pipeline on its own.

## Decision

**The Asana client lives in `common-python-utils` as
`mini_app_polis.asana`, not in this repo.** This cog holds only the
wiring: `clients/asana_client.py` reads Settings and constructs the
shared client. The shared client's `AsanaTaskInput` carries no notion
of voice notes, Drive, or any other source — the only hook a source
gets is `external_id`, an opaque string it namespaces itself
(`voicenote.<drive_file_id>` here, `discord.<message_id>` later). The
Discord path is explicitly **not** built now; this is the seam it will
use, and nothing more.

**Idempotency moves onto Asana's `external` field.** Every task is
created with `external.gid = voicenote.<drive_file_id>`, and the check
before posting is a single `GET /tasks/external:<id>`. The Drive view
URL stays in the body, but only as a link for the operator.

**Bodies become Asana rich text, in the board's card format.**
`compose_html_notes` emits `<body>`-wrapped markup in two regions,
mirroring the "TEMPLATE — do not work" card in the intake column:

    Done when:
    Where: <where>          <- blank when the note had none
    Constraints:
    Effort:
    PR:
    Blocked by:

    <description prose>

    **Peers** / **Timeline** <- only when the note supplied them
    **Audio** <- Drive link

All six template lines are emitted whether or not the note filled them:
an empty `Effort:` is a prompt to fill it during grooming, while an
absent one is a card that cannot be groomed without being reshaped by
hand first. Only `Where` is ever auto-filled — it already means the
system, repo or location the work happens in, which is what the board's
existing `Where: deejaytools` entries record. A definition of done, an
effort estimate and a PR link are grooming decisions a voice note has
no basis to invent, so they are left blank rather than guessed at.

The region below the blank line is the layout carried over from
Todoist. `where` does not repeat there as a `Context` section, since
the template's `Where` field already carries it.

Everything Whisper or Claude produced is escaped on the way in.

**Labels become real tags.** Claude's suggested labels and the `review`
flag are resolved to workspace tag gids via `find_or_create_tag`,
cached for the life of the process.

**Tasks are assigned to the token holder and land in the intake
column.** `assignee="me"` puts every note in My Tasks as well as on the
board; `ASANA_INBOX_SECTION_ID` places it in the intake column, where
notes wait to be groomed.

Todoist support is removed outright rather than kept behind a provider
flag. A provider abstraction with one provider in use is a seam that
nothing exercises, and this pipeline has exactly one sink.

## Consequences

**Better:**
- Deduplication is one request instead of a list-and-scan, and it sees
  completed tasks — the class of duplicate that Todoist's active-only
  list endpoint could not prevent.
- The dedup marker is no longer prose. Reformatting the task body can
  no longer break idempotency.
- Captured notes land on the same board as the rest of the work,
  already in the column the grooming process starts from.
- The next source to create tasks writes no client of its own.

**Worse / to watch:**
- **Escaping is now a correctness concern.** Todoist accepted markdown
  and forgave a stray `<`; Asana's `html_notes` answers 400 on
  malformed markup, and transcripts contain angle brackets. All
  model-supplied text goes through `escape_rich_text`; a test asserts
  it.
- **Tags cost requests.** A tag is a workspace object, not a string, so
  each distinct name is a lookup and possibly a create. Resolution is
  cached per process and deliberately **best-effort**: a tag that fails
  to resolve is logged at WARNING and skipped rather than failing the
  post, because a task whose body is already correct should not be
  stranded — with its audio — over decoration. The review signal
  survives independently in the `_EMPTY_TITLE_FALLBACK` title.
- **`external` is app-scoped.** Asana scopes external data to the app
  that wrote it. For a personal access token that is the token holder's
  own app context; the practical consequence is that idempotency is
  scoped to this credential, which is the desired behaviour but should
  be confirmed in dev before the first production run. If the lookup
  ever proves unreliable, the fallback is the Todoist approach — scan
  the project for the Drive URL, which is still in every body.
- **Four env vars where there were two** (`ASANA_ACCESS_TOKEN`,
  `ASANA_WORKSPACE_ID`, `ASANA_INBOX_PROJECT_ID`,
  `ASANA_INBOX_SECTION_ID`). Only the section is optional.
- **Free tier.** No custom fields, so there is nowhere structured to
  put source metadata; the `external` field is the API-level substitute
  and it is invisible in the UI.

## Correction — September 2026

The first deployed version emitted only the Todoist-era layout
(description prose, then `Context` / `Peers` / `Timeline` / `Audio`) and
no template block, so machine-created cards did not match the board's
existing format and could not be groomed in place. The board's card
template had been agreed before this work started and was visible on
every existing card; it simply was not consulted. The Decision section
above records the corrected format.

The board's card format now lives in `post_task.py` as
`_TEMPLATE_FIELDS` / `render_template_block`. That is the wrong long-term
home — it is a board convention, not a voicenotes one, so the Discord
path would duplicate it. It stays here until there is a second caller,
at which point it moves to `mini_app_polis.asana` alongside the client,
per the principle that a design belongs with its implementation.

## Migration steps

1. Mint a personal access token (My Settings → Apps → Manage Developer
   Apps → Personal Access Tokens).
2. Run `uv run python -m transcription_cog.voicenotes.scripts.setup_asana
   --save-to-doppler` to resolve and store the workspace, project and
   section gids.
3. Release `common-python-utils` 5.3.0 to PyPI; this repo's pin is
   already `>=5.3.0,<6`.
4. Deploy, then record one voice note and confirm the task lands in the
   intake column, assigned, with a working Audio link — and that
   re-triggering the same file does not create a second task.
5. Remove `TODOIST_API_TOKEN` and `TODOIST_INBOX_PROJECT_ID` from
   Doppler once step 4 passes.
