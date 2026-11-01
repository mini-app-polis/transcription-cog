"""Prompt templates loaded at runtime by the clients.

Templates are stored as ``.md`` next to this file. The wheel ships them
via the ``[tool.hatch.build.targets.wheel.force-include]`` block in
the repo-root ``pyproject.toml`` (see ADR-0004). Runtime loaders use
``importlib.resources.files("transcription_cog.voicenotes.prompts")``
to read them.
"""
