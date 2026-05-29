"""Shared helper: classify per-file exceptions as batch-fatal or isolated.

Both pipelines in this cog (voicenotes ingest + WCS transcript ingest)
loop over a batch of files and run the same fundamental decision per
file: keep going, or stop the batch?

The default in both loops is `except Exception` → log the file's
failure and continue. That's the right policy for "this file is bad"
errors (corrupt audio, unsupported format, Drive ACL, prompt-parse
failure). It's the *wrong* policy for "the system is wedged" signals
where every remaining file in the batch would fail with the same root
cause — most importantly:

* The Prefect ``timeout_seconds`` cap firing on the transcribe or
  extract task (raised as ``TimeoutError`` or
  ``prefect.exceptions.TimedOut`` depending on Prefect version).
* Manual cancel of the flow run from the Prefect UI.
* Worker shutdown mid-flight during a Railway redeploy
  (``CancelledError`` / ``TerminationSignal``).

In those cases continuing the loop wastes the Prefect retry budget,
keeps the worker pinned past the redeploy grace window, and produces
N near-identical failure findings instead of one clean abort.

Matching strategy
-----------------

Class names rather than ``isinstance`` against imported types. Prefect
has reorganised ``prefect.exceptions`` across minor versions; matching
by name keeps the classifier working without pinning a specific
Prefect release.

The classifier walks the whole MRO so a subclass of ``TimeoutError``
(or any other fatal type) still trips the check. Third-party SDKs
often wrap socket timeouts in their own subclass — that's still a
"system is wedged" signal and the batch should abort the same way.
"""

from __future__ import annotations

# Per-file exception class names that abort the whole batch instead of
# being isolated. See module docstring for the rationale.
BATCH_FATAL_EXC_NAMES: frozenset[str] = frozenset(
    {
        "TimedOut",  # prefect.exceptions.TimedOut
        "TimeoutError",  # builtin (also raised by Prefect timeout_seconds)
        "CancelledError",  # asyncio / Prefect cancellation
        "FlowRunCancelled",  # explicit flow-run cancel from Prefect UI
        "TerminationSignal",  # Prefect worker shutdown
    }
)


def is_batch_fatal(exc: BaseException) -> bool:
    """Return True if ``exc`` should abort the batch instead of being isolated.

    Matched by class name (rather than ``isinstance`` against imported
    types), walking the whole MRO. The name lookup handles Prefect's
    periodic reorganisation of ``prefect.exceptions``; walking the
    MRO handles third-party SDKs that wrap socket timeouts in their
    own ``TimeoutError`` subclasses.

    The MRO walk is a strict superset of leaf-name matching — a
    direct hit on the leaf class still wins on the first iteration.
    No entry in :data:`BATCH_FATAL_EXC_NAMES` should be a name
    shared by a common non-fatal base class (e.g. ``Exception``,
    ``OSError``) or the classifier would false-positive on
    everything.
    """
    for cls in type(exc).__mro__:
        if cls.__name__ in BATCH_FATAL_EXC_NAMES:
            return True
    return False


__all__ = ["BATCH_FATAL_EXC_NAMES", "is_batch_fatal"]
