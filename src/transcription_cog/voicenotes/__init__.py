"""Voicenotes sub-package — voice sticky-note pipeline.

Drive → Whisper → Claude → the task manager, through the API.

Sentry initialization for this sub-package's errors flows through the
parent cog's Sentry SDK init in ``transcription_cog.worker``; this
module deliberately performs no import-time side effects.

The two pipelines share one deployment, a single Lambda function. See
docs/decisions/ADR-0004-voicenotes-merge.md and ADR-0007.
"""
