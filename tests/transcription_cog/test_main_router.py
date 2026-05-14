"""Tests for the top-level router in ``transcription_cog.main``.

The router (`notes_ingest_router`) is the deployment's entry-point flow.
Every external trigger from watcher-cog, the Prefect UI, or the CLI lands
here first, so its dispatch table is the single contract that decides
which underlying pipeline runs.

These tests verify the contract directly:

  - Each documented ``mode`` value dispatches to the matching flow
    function and returns whatever that function returned.
  - Unknown modes raise a loud ``ValueError`` (rather than silently
    no-op'ing — the inner runtime guard is load-bearing because Prefect's
    Literal-parameter validation happens before the flow body runs, but
    a hand-rolled API call could bypass it).
  - The mode set documented in the module docstring matches the actual
    dispatch table.

We call ``.fn`` to skip Prefect's flow-runner machinery — the router has
no decorators that change its behavior between ``flow_name()`` and
``flow_name.fn()`` (no concurrency slots, no run hooks), so direct
function-body testing is faithful and fast.
"""

from __future__ import annotations

from typing import Any, get_args
from unittest.mock import MagicMock

import pytest

from transcription_cog import main as main_mod
from transcription_cog.main import (
    _MODE_DISPATCH,
    NotesIngestMode,
    notes_ingest_router,
)

# ---------------------------------------------------------------------------
# Dispatch table integrity
# ---------------------------------------------------------------------------


class TestDispatchTable:
    """The dispatch table is the single source of truth — verify its shape."""

    def test_dispatch_table_keys_match_literal_type(self) -> None:
        """``_MODE_DISPATCH`` keys must exactly match the ``NotesIngestMode`` Literal.

        If the two drift, Prefect Cloud's UI dropdown (built from the
        Literal) will offer modes that aren't dispatchable, or callers
        relying on the dict will pass modes Prefect rejects at the
        parameter-validation layer.
        """
        literal_values = set(get_args(NotesIngestMode))
        dispatch_keys = set(_MODE_DISPATCH)
        assert literal_values == dispatch_keys, (
            f"Literal/dispatch drift — literal={literal_values!r}, "
            f"dispatch={dispatch_keys!r}"
        )

    def test_dispatch_table_documents_three_modes(self) -> None:
        """Belt and braces against accidental shrinkage of the dispatch table."""
        assert set(_MODE_DISPATCH) == {
            "wcs-transcripts",
            "voicenotes",
            "voicenotes-cleanup",
        }


# ---------------------------------------------------------------------------
# Per-mode dispatch
# ---------------------------------------------------------------------------


class TestRouterDispatch:
    """Each mode must call its target flow exactly once and return its value.

    Targets are swapped for ``MagicMock`` instances so we never hit the
    real WCS / voicenotes pipelines — those have their own integration
    tests.
    """

    @pytest.fixture
    def stub_dispatch(self, monkeypatch: pytest.MonkeyPatch) -> dict[str, MagicMock]:
        """Replace every dispatch target with a MagicMock returning a sentinel."""
        stubs: dict[str, MagicMock] = {}
        for mode in _MODE_DISPATCH:
            stub = MagicMock(name=f"target[{mode}]", return_value=f"result-for-{mode}")
            stubs[mode] = stub
        # Patch the module-level dict in place so the router uses our stubs.
        monkeypatch.setattr(main_mod, "_MODE_DISPATCH", stubs)
        return stubs

    def test_wcs_transcripts_dispatches_to_process_transcript(
        self,
        stub_dispatch: dict[str, MagicMock],
    ) -> None:
        result = notes_ingest_router.fn(mode="wcs-transcripts")
        stub_dispatch["wcs-transcripts"].assert_called_once_with()
        stub_dispatch["voicenotes"].assert_not_called()
        stub_dispatch["voicenotes-cleanup"].assert_not_called()
        assert result == "result-for-wcs-transcripts"

    def test_voicenotes_dispatches_to_voicenotes_ingest(
        self,
        stub_dispatch: dict[str, MagicMock],
    ) -> None:
        result = notes_ingest_router.fn(mode="voicenotes")
        stub_dispatch["voicenotes"].assert_called_once_with()
        stub_dispatch["wcs-transcripts"].assert_not_called()
        stub_dispatch["voicenotes-cleanup"].assert_not_called()
        assert result == "result-for-voicenotes"

    def test_voicenotes_cleanup_dispatches_to_voicenotes_cleanup(
        self,
        stub_dispatch: dict[str, MagicMock],
    ) -> None:
        result = notes_ingest_router.fn(mode="voicenotes-cleanup")
        stub_dispatch["voicenotes-cleanup"].assert_called_once_with()
        stub_dispatch["wcs-transcripts"].assert_not_called()
        stub_dispatch["voicenotes"].assert_not_called()
        assert result == "result-for-voicenotes-cleanup"


# ---------------------------------------------------------------------------
# Error handling
# ---------------------------------------------------------------------------


class TestRouterErrors:
    """The router must fail loudly on unknown modes, never silently no-op."""

    @pytest.fixture(autouse=True)
    def _isolate_dispatch(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """Replace real targets with stubs so a successful path can't accidentally fire."""
        stubs: dict[str, Any] = {
            mode: MagicMock(name=f"target[{mode}]") for mode in _MODE_DISPATCH
        }
        monkeypatch.setattr(main_mod, "_MODE_DISPATCH", stubs)

    def test_unknown_mode_raises_value_error(self) -> None:
        """Any non-dispatch key must hit the inner runtime guard.

        At runtime, Prefect's Literal validation catches typos before the
        flow body runs — but the router's runtime guard is the safety
        net for hand-rolled API calls (which bypass the schema) and
        internal callers that go straight through ``.fn(...)``.
        """
        with pytest.raises(ValueError, match="Unknown transcription-cog router mode"):
            notes_ingest_router.fn(mode="bogus")  # type: ignore[arg-type]

    def test_unknown_mode_error_lists_supported_modes(self) -> None:
        """Error message must include the supported-mode list for operator clarity."""
        with pytest.raises(ValueError) as exc_info:
            notes_ingest_router.fn(mode="not-a-real-mode")  # type: ignore[arg-type]
        message = str(exc_info.value)
        # All three modes should appear in the message, in any order.
        for mode in ("wcs-transcripts", "voicenotes", "voicenotes-cleanup"):
            assert mode in message, f"Missing {mode!r} in error message: {message!r}"

    def test_empty_string_mode_raises(self) -> None:
        """Edge case: empty string is not a valid mode."""
        with pytest.raises(ValueError, match="Unknown transcription-cog router mode"):
            notes_ingest_router.fn(mode="")  # type: ignore[arg-type]
