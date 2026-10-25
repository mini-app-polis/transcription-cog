# ADR-0008: Hand voice-note tasks to the task manager instead of writing to the board

## Status

Accepted. Supersedes ADR-0006's write path; the Todoist-to-Asana move it records
still stands.

## Context

A voice note became a task by this cog writing to Asana itself
(`tasks/post_task.py`): composing the board's template block, resolving tags,
retrying Asana with tenacity, and holding the Asana token and board ids.

The board now has more than one source of tasks, and one writer for all of
them: task-manager-cog. It owns what every task has in common (the template
block, the task id, tags, column and assignee) and is reached through
`POST /v1/tasks` on api-kaianolevine-com, which validates the request against
the shared contract (`mini_app_polis.api.contract.TaskCreateRequest`) and
queues it. A second writer would mint ids the task manager does not see and
carry its own copy of the board's shape.

## Decision

This cog does what only it can: transcribe the audio and extract the task with
its one model call. It then hands the result over as a `TaskDraft`
(`tasks/request_task.py`) and makes no Asana call.

- The draft carries the title, `where`, the description as its body, Peers and
  Timeline as sections, the audio as a link, the labels, `needs_review`, and
  today as the due date.
- The external id stays `voicenote.<drive_file_id>`. The task manager looks it
  up before creating anything, so a redelivered job asks again and makes no
  second task, including for tasks created by the old path.
- The audio is archived only after the API answers 202. A refused request
  raises, leaves the file in the inbox, and the queue redelivers it.
- The shared API client retries transient failures, so tenacity goes.

## Consequences

- The Asana token and board ids leave this cog's configuration and its Lambda.
- The task is created after this run ends. This run's report records the
  request; the task manager's report names the task and links to it.
- The run budget no longer includes Asana: a task request is about 100 s at
  worst, against about 190 s for the Asana path it replaces.
