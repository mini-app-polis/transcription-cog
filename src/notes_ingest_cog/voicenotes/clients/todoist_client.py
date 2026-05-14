"""Todoist API client.

Auth model: a single long-lived personal API token, copied from
Todoist's web UI (Settings → Integrations → Developer). The token
does not expire until the operator explicitly revokes it, so this
client has no OAuth machinery — every request just sends the token
as a Bearer credential.

This client deliberately covers only the surface voicenotes-cog
needs:

  - ``create_task`` — POST /api/v1/tasks
  - ``find_task_by_drive_file_id`` — GET /api/v1/tasks?project_id=…
    + client-side scan for the embedded ``drive_file_id`` marker
    (Todoist's ``/tasks`` query parameters don't support
    full-text search)

API surface note: Todoist's older ``/rest/v2/`` REST API was
deprecated in 2025 and returns HTTP 410. The unified ``/api/v1/``
endpoints below replace it. Auth (Bearer + personal API token) is
unchanged. List endpoints now wrap their payload as
``{"results": [...], "next_cursor": ...}`` rather than returning
a bare array — see ``find_task_by_drive_file_id``.

Reference: https://developer.todoist.com/api/v1/
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date

import httpx

from notes_ingest_cog.voicenotes._shared import get_logger
from notes_ingest_cog.voicenotes.config import settings

# Todoist unified v1 API base URL. The older /rest/v2/ endpoints
# return HTTP 410 as of 2025.
_TODOIST_API_BASE = "https://api.todoist.com/api/v1"

_logger = get_logger("voicenotes-cog")


@dataclass(frozen=True)
class TodoistTaskInput:
    """Input shape for creating a Todoist task.

    Decoupled from ``ExtractedTask`` so that the post_task layer can
    compose extra fields (labels, project_id) without leaking
    Todoist concerns into ``ExtractedTask``.

    Field-name note: in the Todoist API, ``content`` is the task
    *title* and ``description`` is the body. We use unambiguous
    Python names (``title`` / ``description``) on this dataclass and
    do the rename inside ``create_task`` so callers never have to
    think about Todoist's terminology.
    """

    title: str
    """Task name. Sent to Todoist as the ``content`` field."""

    description: str
    """Long body. Sent to Todoist as the ``description`` field."""

    project_id: str
    """Todoist project ID (the equivalent of a list)."""

    due_date: date | None = None
    labels: tuple[str, ...] = ()


class TodoistAuthError(RuntimeError):
    """Raised on 401/403 from Todoist.

    The stored API token is invalid or has been revoked. Operator
    must mint a new token (Settings → Integrations → Developer) and
    rotate ``TODOIST_API_TOKEN`` in Doppler. Prefect should NOT
    retry on this — every retry will fail the same way until a
    human intervenes; surface to Sentry and stop.
    """


class TodoistAPIError(RuntimeError):
    """Raised on non-auth API errors (network, 5xx). Prefect will retry."""


class TodoistClient:
    """Thin Todoist API wrapper.

    Auth is a single long-lived bearer token read from Settings on
    every call (so secret rotation in Doppler takes effect on the
    next request without a process restart, given Settings is
    re-resolved per request).
    """

    def __init__(self, *, http_client: httpx.Client | None = None) -> None:
        self._http: httpx.Client = http_client or httpx.Client(
            timeout=settings.http_timeout_seconds
        )

    # ------------------------------------------------------------------
    # Headers
    # ------------------------------------------------------------------

    def _auth_headers(self) -> dict[str, str]:
        return {
            "Authorization": f"Bearer {settings.todoist_api_token}",
            "Content-Type": "application/json",
        }

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def create_task(self, task: TodoistTaskInput) -> str:
        """Create a task in Todoist. Returns the new task's ID.

        Maps ``TodoistTaskInput`` → Todoist API payload, doing the
        ``title`` → ``content`` rename so callers never have to
        think about Todoist's field names.
        """
        payload: dict[str, object] = {
            "content": task.title,
            "description": task.description,
            "project_id": task.project_id,
        }
        if task.due_date is not None:
            # Todoist accepts due_date as YYYY-MM-DD; the user's
            # account timezone setting handles display.
            payload["due_date"] = task.due_date.isoformat()
        if task.labels:
            payload["labels"] = list(task.labels)

        response = self._post(f"{_TODOIST_API_BASE}/tasks", json_payload=payload)

        body = response.json()
        task_id = body.get("id") if isinstance(body, dict) else None
        if not isinstance(task_id, str):
            raise TodoistAPIError(
                f"Todoist create_task did not return an id; got: {body!r}"
            )
        return task_id

    def find_task_by_drive_file_id(
        self,
        project_id: str,
        drive_file_id_marker: str,
    ) -> str | None:
        """Best-effort idempotency check.

        Lists active tasks in the given project and scans each
        task's ``description`` for the marker substring. Returns the
        existing task ID if found, else ``None``. At ~30 tasks/month
        this is cheap.

        ``drive_file_id_marker`` is any substring that uniquely
        identifies a task's source audio — currently the Drive view
        URL embedded in the audio link footer (e.g.
        ``https://drive.google.com/file/d/<id>/view``). Caller
        applies the template; this method is ignorant of marker
        format and just does substring matching.

        Note: Todoist's GET /tasks returns only active (uncompleted)
        tasks. If the operator checked off a duplicate before the
        retry fired, we'd miss it and post a new copy. Acceptable
        trade-off given the lost-note-prevention North Star.

        Pagination: at this volume (~30 tasks/month, archive on
        check-off) the active-task list is well below any plausible
        page size, so we read only the first page. If a future
        scaling change makes this matter, follow ``next_cursor``.
        """
        response = self._get(f"{_TODOIST_API_BASE}/tasks?project_id={project_id}")
        body = response.json()
        # Unified v1 wraps results in {"results": [...], "next_cursor": ...}.
        # Accept a bare list too in case Todoist tweaks the envelope.
        if isinstance(body, dict) and isinstance(body.get("results"), list):
            tasks = body["results"]
        elif isinstance(body, list):
            tasks = body
        else:
            raise TodoistAPIError(f"Todoist /tasks returned unexpected shape: {body!r}")

        for t in tasks:
            if not isinstance(t, dict):
                continue
            description = t.get("description") or ""
            if drive_file_id_marker in description:
                tid = t.get("id")
                if isinstance(tid, str):
                    return tid
        return None

    # ------------------------------------------------------------------
    # HTTP helpers
    # ------------------------------------------------------------------

    def _post(self, url: str, *, json_payload: dict[str, object]) -> httpx.Response:
        response = self._http.post(url, headers=self._auth_headers(), json=json_payload)
        self._raise_for_status(response)
        return response

    def _get(self, url: str) -> httpx.Response:
        response = self._http.get(url, headers=self._auth_headers())
        self._raise_for_status(response)
        return response

    @staticmethod
    def _raise_for_status(response: httpx.Response) -> None:
        if 200 <= response.status_code < 300:
            return
        if response.status_code in (401, 403):
            _logger.error(
                "todoist_auth.failure",
                category="api",
                context={
                    "status_code": response.status_code,
                    "body_preview": response.text[:200],
                },
            )
            raise TodoistAuthError(
                f"Todoist auth failure {response.status_code}: "
                f"{response.text[:200]}. Mint a new token at "
                "https://app.todoist.com/app/settings/integrations/developer "
                "and rotate TODOIST_API_TOKEN in Doppler."
            )
        raise TodoistAPIError(
            f"Todoist API {response.status_code}: {response.text[:200]}"
        )


# ---------------------------------------------------------------------------
# Module-level singleton
# ---------------------------------------------------------------------------

_client: TodoistClient | None = None


def get_todoist_client() -> TodoistClient:
    """Return the lazily-instantiated module-level TodoistClient singleton."""
    global _client
    if _client is None:
        _client = TodoistClient()
    return _client


def reset_todoist_client() -> None:
    """Force a new client on next call.

    For Todoist auth failures the actual fix is to rotate
    ``TODOIST_API_TOKEN`` in Doppler — this resetter only discards
    the in-process httpx state, not any cached secret.
    """
    global _client
    _client = None
