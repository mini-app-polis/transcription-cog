"""Tests for the voicenotes-router dispatch flow."""

from __future__ import annotations

import pytest

from notes_ingest_cog.voicenotes.flows import router as router_mod
from notes_ingest_cog.voicenotes.flows.router import (
    MODE_CLEANUP,
    MODE_INGEST,
    voicenotes_router,
)


class TestRouterIngestMode:
    """Router with mode='ingest' delegates to voicenotes_ingest."""

    def test_ingest_calls_ingest_flow(self, monkeypatch):
        """Router invokes the ingest flow exactly once and returns its result."""
        called = {"count": 0}

        def fake_ingest():
            """Stub for voicenotes_ingest."""
            called["count"] += 1
            return {"files_processed": 1, "files_failed": 0}

        monkeypatch.setattr(router_mod, "voicenotes_ingest", fake_ingest)

        result = voicenotes_router.fn(mode=MODE_INGEST)

        assert called["count"] == 1
        assert result == {"files_processed": 1, "files_failed": 0}

    def test_ingest_is_default_mode(self, monkeypatch):
        """No mode parameter passed → defaults to ingest, not cleanup."""
        called = {"ingest": 0, "cleanup": 0}

        def fake_ingest():
            """Stub for voicenotes_ingest."""
            called["ingest"] += 1
            return {}

        def fake_cleanup():
            """Stub for voicenotes_cleanup."""
            called["cleanup"] += 1
            return {}

        monkeypatch.setattr(router_mod, "voicenotes_ingest", fake_ingest)
        monkeypatch.setattr(router_mod, "voicenotes_cleanup", fake_cleanup)

        voicenotes_router.fn()

        assert called == {"ingest": 1, "cleanup": 0}


class TestRouterCleanupMode:
    """Router with mode='cleanup' delegates to voicenotes_cleanup."""

    def test_cleanup_calls_cleanup_flow(self, monkeypatch):
        """Router invokes the cleanup flow exactly once and returns its result."""
        called = {"count": 0}

        def fake_cleanup():
            """Stub for voicenotes_cleanup."""
            called["count"] += 1
            return {"deleted": 0, "failed": 0}

        monkeypatch.setattr(router_mod, "voicenotes_cleanup", fake_cleanup)

        result = voicenotes_router.fn(mode=MODE_CLEANUP)

        assert called["count"] == 1
        assert result == {"deleted": 0, "failed": 0}


class TestRouterExceptionPropagation:
    """Exceptions from the dispatched sub-flow must not be swallowed."""

    def test_ingest_exception_propagates(self, monkeypatch):
        """A RuntimeError inside the ingest sub-flow propagates to the caller."""

        def boom():
            """Stub that raises to simulate ingest failure."""
            raise RuntimeError("ingest blew up")

        monkeypatch.setattr(router_mod, "voicenotes_ingest", boom)
        with pytest.raises(RuntimeError, match="ingest blew up"):
            voicenotes_router.fn(mode=MODE_INGEST)

    def test_cleanup_exception_propagates(self, monkeypatch):
        """A RuntimeError inside the cleanup sub-flow propagates to the caller."""

        def boom():
            """Stub that raises to simulate cleanup failure."""
            raise RuntimeError("cleanup blew up")

        monkeypatch.setattr(router_mod, "voicenotes_cleanup", boom)
        with pytest.raises(RuntimeError, match="cleanup blew up"):
            voicenotes_router.fn(mode=MODE_CLEANUP)


class TestRouterUnknownMode:
    """An unrecognised mode string must raise a clear ValueError."""

    def test_unknown_mode_raises(self):
        """Passing mode='invalid' raises ValueError with 'unknown mode' message."""
        with pytest.raises(ValueError, match="unknown mode"):
            voicenotes_router.fn(mode="invalid")
