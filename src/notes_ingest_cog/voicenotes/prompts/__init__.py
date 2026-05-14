"""Prompt templates loaded at runtime by the clients.

Templates are stored as ``.md`` next to this file. The wheel ships them
via the ``[tool.hatch.build.targets.wheel.force-include]`` block in
the repo-root ``pyproject.toml`` (pin added when voicenotes-cog was
merged in — see ADR-004). Runtime loaders use
``importlib.resources.files("notes_ingest_cog.voicenotes.prompts")``
to read them.
"""
