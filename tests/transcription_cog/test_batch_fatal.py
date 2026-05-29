"""Tests for the shared batch-fatal exception classifier.

The classifier decides whether a per-file exception in a batch loop
should abort the whole batch or be isolated. See
``transcription_cog/_batch_fatal.py`` for the rationale; these tests
pin the contract both pipelines (voicenotes ingest + WCS transcript
ingest) rely on.
"""

from __future__ import annotations

import pytest

from transcription_cog._batch_fatal import (
    BATCH_FATAL_EXC_NAMES,
    is_batch_fatal,
)


class TestBatchFatal:
    """``is_batch_fatal`` matches by class name across Prefect versions."""

    @pytest.mark.parametrize(
        "cls_name",
        sorted(BATCH_FATAL_EXC_NAMES),
    )
    def test_named_classes_are_fatal(self, cls_name: str) -> None:
        """Each entry in BATCH_FATAL_EXC_NAMES is detected as fatal.

        Synthesises a dynamic class with the right name so we don't
        have to import every Prefect-version-specific type. The
        classifier intentionally matches by name to survive Prefect's
        periodic ``prefect.exceptions`` reshuffles.
        """
        exc_cls = type(cls_name, (Exception,), {})
        assert is_batch_fatal(exc_cls()) is True

    def test_builtin_timeout_error_is_fatal(self) -> None:
        """Python's builtin ``TimeoutError`` — what Prefect raises when
        a task hits ``timeout_seconds`` — must abort the batch."""
        assert is_batch_fatal(TimeoutError("task timed out")) is True

    @pytest.mark.parametrize(
        "cls_name",
        [
            "ValueError",
            "RuntimeError",
            "KeyError",
            "RateLimitError",
            "APIStatusError",
            "HTTPError",
            "FilenameParseError",
            "LLMTruncationError",
        ],
    )
    def test_ordinary_exceptions_are_not_fatal(self, cls_name: str) -> None:
        """Per-file errors (bad audio, parse failure, transient HTTP)
        stay isolated so one bad file doesn't drop the rest of the
        batch."""
        exc_cls = type(cls_name, (Exception,), {})
        assert is_batch_fatal(exc_cls()) is False

    def test_subclass_of_real_timeouterror_is_fatal(self) -> None:
        """Subclassing the builtin TimeoutError preserves the name
        match — most user-defined "this took too long" exceptions
        derive from it."""

        class MyAppTimeoutError(TimeoutError):
            pass

        assert is_batch_fatal(MyAppTimeoutError()) is True

    def test_unrelated_exception_named_TimedOut_is_fatal(self) -> None:
        """Name-based matching means even an unrelated class hierarchy
        is treated as fatal as long as the name matches. This is the
        intent — we want to catch ``prefect.exceptions.TimedOut`` no
        matter how Prefect reorganises its exception module across
        versions."""

        class TimedOut(Exception):  # noqa: N818 — match Prefect's name
            pass

        assert is_batch_fatal(TimedOut()) is True
