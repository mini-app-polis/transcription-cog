"""Voicenotes sub-package — voice sticky-note pipeline.

Drive → Whisper → Claude → Asana.

Sentry initialization for this sub-package's errors flows through the
parent cog's Sentry SDK init in ``transcription_cog.worker``; this
module deliberately performs no import-time side effects.

Originally lived as a standalone ``voicenotes-cog`` repository.
Consolidated into transcription-cog (May 2026) so the two pipelines
share one deployment. See docs/decisions/ADR-004-voicenotes-merge.md;
the deployment is now one Lambda function (ADR-007).
"""
