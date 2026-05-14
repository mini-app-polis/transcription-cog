"""Voicenotes sub-package — voice sticky-note pipeline.

Drive → Whisper → Claude → Todoist.

Imported here from the merged transcription-cog deployment. Sentry
initialization for this sub-package's errors flows through the
parent cog's Sentry SDK init in ``transcription_cog.main``; this
module deliberately performs no import-time side effects.

Originally lived as a standalone ``voicenotes-cog`` repository.
Consolidated into transcription-cog (May 2026) so the two pipelines
share a single Prefect deployment and Railway service. See
docs/decisions/ADR-004-voicenotes-merge.md.
"""
