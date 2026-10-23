"""Adapter to ``common-python-utils`` (import name: ``mini_app_polis``).

Centralizes every dependency on the shared library so that:

1. Production code never imports from ``mini_app_polis`` directly —
   it imports from this module. If the upstream package renames or
   restructures something, the blast radius is one file.
2. We bridge gaps between what the cog needs and what upstream
   currently provides. The structured-event logger (CD-009 shape) is
   the main case: upstream exposes a stdlib ``logging.Logger`` with
   emoji prefixes, not a structured emitter, so we serialize
   structured fields to a JSON string and hand them to the upstream
   logger as the message.

Upstream API used (common-python-utils v2.5.0):

- ``mini_app_polis.logger.get_logger() -> logging.Logger``
- ``mini_app_polis.google.GoogleAPI.from_env() -> GoogleAPI``
  with ``.drive`` (``DriveFacade``) exposing ``download_file_bytes``,
  ``move_file``, ``delete_file``, ``ensure_folder``, ``list_files``,
  ``find_file_in_folder``.
"""

from __future__ import annotations

import json
import logging
from datetime import UTC, datetime
from typing import Any, Protocol

from mini_app_polis import logger as _upstream_logger_mod


class StructuredLogger(Protocol):
    """The logger surface we use across the cog. Mirrors CD-009."""

    def debug(
        self,
        event: str,
        *,
        category: str = ...,
        context: dict[str, Any] | None = ...,
    ) -> None:
        """Emit a DEBUG-level structured event."""
        ...

    def info(
        self,
        event: str,
        *,
        category: str = ...,
        context: dict[str, Any] | None = ...,
    ) -> None:
        """Emit an INFO-level structured event."""
        ...

    def warning(
        self,
        event: str,
        *,
        category: str = ...,
        context: dict[str, Any] | None = ...,
    ) -> None:
        """Emit a WARNING-level structured event."""
        ...

    def error(
        self,
        event: str,
        *,
        category: str = ...,
        context: dict[str, Any] | None = ...,
    ) -> None:
        """Emit an ERROR-level structured event."""
        ...


# ---------------------------------------------------------------------------
# Logger
# ---------------------------------------------------------------------------


def _format_record(
    *,
    service: str,
    level: str,
    event: str,
    category: str,
    context: dict[str, Any] | None,
) -> dict[str, Any]:
    return {
        "timestamp": datetime.now(UTC).isoformat(),
        "service": service,
        "level": level,
        "category": category,
        "event": event,
        "context": context or {},
    }


class _UpstreamLoggerAdapter:
    """Wrap upstream's stdlib logger so cog code can emit CD-009 events.

    Upstream gives us ``logging.Logger`` with emoji prefix conventions.
    We serialize the structured fields to JSON and pass that as the
    message body. Log aggregators that index JSON message bodies (which
    most do) will pick the structure back up.

    When upstream ships a real structured logger, this whole class
    becomes a one-line delegate.
    """

    def __init__(self, upstream: logging.Logger, service: str) -> None:
        self._upstream = upstream
        self._service = service

    def _emit(
        self,
        level_method: str,
        event: str,
        category: str,
        context: dict[str, Any] | None,
    ) -> None:
        record = _format_record(
            service=self._service,
            level=level_method.upper(),
            event=event,
            category=category,
            context=context,
        )
        getattr(self._upstream, level_method)(json.dumps(record))

    def debug(
        self,
        event: str,
        *,
        category: str = "infra",
        context: dict[str, Any] | None = None,
    ) -> None:
        """Emit a DEBUG-level structured event."""
        self._emit("debug", event, category, context)

    def info(
        self,
        event: str,
        *,
        category: str = "infra",
        context: dict[str, Any] | None = None,
    ) -> None:
        """Emit an INFO-level structured event."""
        self._emit("info", event, category, context)

    def warning(
        self,
        event: str,
        *,
        category: str = "infra",
        context: dict[str, Any] | None = None,
    ) -> None:
        """Emit a WARNING-level structured event."""
        self._emit("warning", event, category, context)

    def error(
        self,
        event: str,
        *,
        category: str = "infra",
        context: dict[str, Any] | None = None,
    ) -> None:
        """Emit an ERROR-level structured event."""
        self._emit("error", event, category, context)


def get_logger(service: str = "voicenotes-cog") -> StructuredLogger:
    """Return the structured logger for this cog.

    Wraps upstream's stdlib logger so cog code emits CD-009-shaped
    events.
    """
    return _UpstreamLoggerAdapter(_upstream_logger_mod.get_logger(), service=service)


# ---------------------------------------------------------------------------
# Drive
# ---------------------------------------------------------------------------

try:  # pragma: no cover — import-path branch
    from mini_app_polis.google import GoogleAPI as _UpstreamGoogleAPI

    _USING_UPSTREAM_DRIVE = True
except ImportError:  # pragma: no cover — fallback branch
    _UpstreamGoogleAPI = None  # type: ignore[assignment, misc]
    _USING_UPSTREAM_DRIVE = False


def get_google_api() -> Any:
    """Return ``GoogleAPI.from_env()`` from upstream.

    Raises a clear error if the shared library isn't installed —
    silently no-op'ing Drive is worse than failing.
    """
    if _USING_UPSTREAM_DRIVE and _UpstreamGoogleAPI is not None:
        return _UpstreamGoogleAPI.from_env()  # type: ignore[no-any-return]

    raise RuntimeError(
        "mini_app_polis (common-python-utils) is not installed; cannot "
        "construct GoogleAPI. Install via `uv sync --all-extras` (requires "
        "git access) or use mocked clients in tests."
    )


# ---------------------------------------------------------------------------
# api-kaianolevine-com client (named API key, see CD-012)
# ---------------------------------------------------------------------------

try:  # pragma: no cover — import-path branch
    from mini_app_polis.api import KaianoApiClient as _UpstreamKaianoApiClient
    from mini_app_polis.api import KaianoApiError as _UpstreamKaianoApiError

    _USING_UPSTREAM_API = True
except ImportError:  # pragma: no cover — fallback branch
    _UpstreamKaianoApiClient = None  # type: ignore[assignment, misc]
    _UpstreamKaianoApiError = Exception  # type: ignore[assignment, misc]
    _USING_UPSTREAM_API = False


# Re-export so consumers can `except KaianoApiError` without
# importing from upstream directly.
KaianoApiError: type[Exception] = _UpstreamKaianoApiError


def get_kaiano_api_client(
    *,
    base_url: str | None = None,
) -> Any:
    """Return a ``KaianoApiClient`` configured from explicit values.

    The upstream client falls back to env vars if these aren't passed.
    We pass them explicitly so every secret flows through
    pydantic-settings + Doppler — Settings is the single source of
    truth.

    Raises a clear error if upstream isn't installed; there's no
    safe local fallback (silently no-op'ing POSTs to the API would
    mask production drift).
    """
    if _USING_UPSTREAM_API and _UpstreamKaianoApiClient is not None:
        return _UpstreamKaianoApiClient(
            base_url=base_url,
            machine_name="transcription-cog",
        )

    raise RuntimeError(
        "mini_app_polis (common-python-utils) is not installed; cannot "
        "construct KaianoApiClient. Install via `uv sync --all-extras`."
    )


def using_upstream() -> dict[str, bool]:
    """Diagnostic — which subsystems are using the upstream package."""
    return {
        "logger": True,
        "drive": _USING_UPSTREAM_DRIVE,
        "api": _USING_UPSTREAM_API,
    }


__all__ = [
    "KaianoApiError",
    "StructuredLogger",
    "get_google_api",
    "get_kaiano_api_client",
    "get_logger",
    "using_upstream",
]
