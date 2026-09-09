"""Asana client wiring for the voicenotes sub-pipeline.

The client itself lives in ``mini_app_polis.asana`` (common-python-utils)
so that the Discord-sourced task path planned on
``api-kaianolevine-com`` consumes the same one rather than a second
copy. This module is only the seam: it reads this cog's Settings and
hands them to the shared constructor, and holds the process-lifetime
singleton so the shared client's tag cache survives across files in a
batch.

Auth is a long-lived personal access token (My Settings → Apps →
Manage Developer Apps → Personal Access Tokens). The shared client
re-reads ``ASANA_ACCESS_TOKEN`` on every request, so rotating it in
Doppler takes effect without a redeploy.
"""

from __future__ import annotations

from mini_app_polis.asana import AsanaClient

from transcription_cog.voicenotes.config import settings

_client: AsanaClient | None = None


def get_asana_client() -> AsanaClient:
    """Return the lazily-instantiated module-level ``AsanaClient``."""
    global _client
    if _client is None:
        _client = AsanaClient(
            access_token=settings.asana_access_token or None,
            workspace_gid=settings.asana_workspace_id or None,
            timeout=settings.http_timeout_seconds,
        )
    return _client


def reset_asana_client() -> None:
    """Force a new client on next call.

    Discards in-process httpx state and the resolved-tag cache. It does
    not re-read any secret: the token is resolved per request inside the
    shared client, so an auth failure is fixed by rotating
    ``ASANA_ACCESS_TOKEN`` in Doppler, not by calling this.
    """
    global _client
    _client = None
