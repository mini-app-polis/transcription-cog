"""Tests for Todoist client — create_task, idempotency lookup, error mapping."""

from __future__ import annotations

import json
from datetime import date

import httpx
import pytest

from notes_ingest_cog.voicenotes.clients.todoist_client import (
    TodoistAPIError,
    TodoistAuthError,
    TodoistClient,
    TodoistTaskInput,
)


def _make_client(*, transport: httpx.MockTransport) -> TodoistClient:
    """Build a TodoistClient backed by the given httpx MockTransport."""
    return TodoistClient(http_client=httpx.Client(transport=transport))


class TestCreateTask:
    """create_task POSTs to /api/v1/tasks and returns the new task id."""

    def test_happy_path_returns_task_id(self):
        """A 200 response with {'id': ...} returns the id string."""
        captured: list[httpx.Request] = []

        def handler(request: httpx.Request) -> httpx.Response:
            """MockTransport handler — captures the outgoing request."""
            captured.append(request)
            return httpx.Response(200, json={"id": "td-101"})

        c = _make_client(transport=httpx.MockTransport(handler))
        task_id = c.create_task(
            TodoistTaskInput(
                title="Send report",
                description="Body",
                project_id="proj-1",
            )
        )
        assert task_id == "td-101"
        assert len(captured) == 1
        # Bearer header is set from settings.todoist_api_token (test fixture).
        assert captured[0].headers["Authorization"].startswith("Bearer ")

    def test_payload_renames_title_to_content(self):
        """TodoistTaskInput.title is mapped to Todoist's 'content' field."""
        captured: list[dict] = []

        def handler(request: httpx.Request) -> httpx.Response:
            """MockTransport handler — captures the JSON body."""
            captured.append(json.loads(request.content))
            return httpx.Response(200, json={"id": "td-102"})

        c = _make_client(transport=httpx.MockTransport(handler))
        c.create_task(
            TodoistTaskInput(
                title="My title",
                description="My body",
                project_id="proj-1",
            )
        )
        body = captured[0]
        assert body["content"] == "My title"
        assert body["description"] == "My body"
        assert body["project_id"] == "proj-1"

    def test_due_date_serialized_yyyy_mm_dd(self):
        """A datetime.date due_date is serialized as YYYY-MM-DD."""
        captured: list[dict] = []

        def handler(request: httpx.Request) -> httpx.Response:
            """MockTransport handler — captures the JSON body."""
            captured.append(json.loads(request.content))
            return httpx.Response(200, json={"id": "td-103"})

        c = _make_client(transport=httpx.MockTransport(handler))
        c.create_task(
            TodoistTaskInput(
                title="t",
                description="d",
                project_id="p",
                due_date=date(2026, 5, 15),
            )
        )
        assert captured[0]["due_date"] == "2026-05-15"

    def test_labels_passed_as_list(self):
        """A tuple of labels is serialized as a JSON list."""
        captured: list[dict] = []

        def handler(request: httpx.Request) -> httpx.Response:
            """MockTransport handler — captures the JSON body."""
            captured.append(json.loads(request.content))
            return httpx.Response(200, json={"id": "td-104"})

        c = _make_client(transport=httpx.MockTransport(handler))
        c.create_task(
            TodoistTaskInput(
                title="t",
                description="d",
                project_id="p",
                labels=("review",),
            )
        )
        assert captured[0]["labels"] == ["review"]

    def test_missing_id_in_response_raises_api_error(self):
        """A 200 response without an 'id' field raises TodoistAPIError."""

        def handler(request: httpx.Request) -> httpx.Response:
            """MockTransport handler — returns malformed payload."""
            return httpx.Response(200, json={"unexpected": "shape"})

        c = _make_client(transport=httpx.MockTransport(handler))
        with pytest.raises(TodoistAPIError):
            c.create_task(TodoistTaskInput(title="t", description="d", project_id="p"))


class TestFindTaskByDriveFileId:
    """find_task_by_drive_file_id scans active tasks for the dedup marker."""

    def test_returns_id_when_marker_in_description(self):
        """A task whose description contains the marker → its id is returned.

        The marker is whatever substring the caller chooses; in
        production this is the Drive view URL of the source audio
        (see ``post_task.DRIVE_FILE_VIEW_URL_TEMPLATE``). This test
        uses the same shape to stay realistic.
        """
        marker = "https://drive.google.com/file/d/abc/view"

        def handler(request: httpx.Request) -> httpx.Response:
            """MockTransport handler — returns matching + non-matching task."""
            assert "project_id=proj-1" in str(request.url)
            return httpx.Response(
                200,
                json=[
                    {"id": "td-1", "description": "no marker"},
                    {"id": "td-2", "description": f"...{marker}..."},
                ],
            )

        c = _make_client(transport=httpx.MockTransport(handler))
        result = c.find_task_by_drive_file_id("proj-1", marker)
        assert result == "td-2"

    def test_returns_none_when_no_match(self):
        """No active task contains the marker → returns None."""

        def handler(request: httpx.Request) -> httpx.Response:
            """MockTransport handler — returns tasks without the marker."""
            return httpx.Response(
                200,
                json=[
                    {"id": "td-1", "description": "nothing"},
                    {"id": "td-2", "description": None},
                ],
            )

        c = _make_client(transport=httpx.MockTransport(handler))
        assert c.find_task_by_drive_file_id("proj-1", "marker-xyz") is None

    def test_unexpected_response_shape_raises_api_error(self):
        """A non-list response from /tasks raises TodoistAPIError."""

        def handler(request: httpx.Request) -> httpx.Response:
            """MockTransport handler — returns a dict where a list is expected."""
            return httpx.Response(200, json={"not": "a list"})

        c = _make_client(transport=httpx.MockTransport(handler))
        with pytest.raises(TodoistAPIError):
            c.find_task_by_drive_file_id("p", "m")


class TestErrorMapping:
    """HTTP status codes are mapped to TodoistAuthError vs. TodoistAPIError."""

    def test_401_raises_auth_error(self):
        """401 from Todoist → TodoistAuthError (operator re-mint required)."""

        def handler(request: httpx.Request) -> httpx.Response:
            """MockTransport handler — returns 401."""
            return httpx.Response(401, text="invalid_token")

        c = _make_client(transport=httpx.MockTransport(handler))
        with pytest.raises(TodoistAuthError):
            c.create_task(TodoistTaskInput(title="t", description="d", project_id="p"))

    def test_403_raises_auth_error(self):
        """403 from Todoist → TodoistAuthError (same recovery as 401)."""

        def handler(request: httpx.Request) -> httpx.Response:
            """MockTransport handler — returns 403."""
            return httpx.Response(403, text="forbidden")

        c = _make_client(transport=httpx.MockTransport(handler))
        with pytest.raises(TodoistAuthError):
            c.find_task_by_drive_file_id("p", "m")

    def test_500_raises_api_error(self):
        """500 from Todoist → TodoistAPIError (Prefect retry-eligible)."""

        def handler(request: httpx.Request) -> httpx.Response:
            """MockTransport handler — returns 500."""
            return httpx.Response(500, text="server error")

        c = _make_client(transport=httpx.MockTransport(handler))
        with pytest.raises(TodoistAPIError):
            c.create_task(TodoistTaskInput(title="t", description="d", project_id="p"))
