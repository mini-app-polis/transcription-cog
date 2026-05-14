"""Tests for config.py — normalization and failure paths."""

from __future__ import annotations

import pytest

from transcription_cog.config import load_config


def test_load_config_missing_required_var(monkeypatch: pytest.MonkeyPatch) -> None:
    """Missing required env var raises RuntimeError with the var name."""
    monkeypatch.delenv("NOTES_INPUT_FOLDER_ID", raising=False)
    with pytest.raises(RuntimeError, match="NOTES_INPUT_FOLDER_ID"):
        load_config()


def test_load_config_invalid_provider(monkeypatch: pytest.MonkeyPatch) -> None:
    """Unsupported LLM_PROVIDER raises RuntimeError."""
    monkeypatch.setenv("LLM_PROVIDER", "gemini")
    monkeypatch.setenv("NOTES_INPUT_FOLDER_ID", "folder-id")
    monkeypatch.setenv("NOTES_PROCESSED_FOLDER_ID", "processed-id")
    monkeypatch.setenv("KAIANO_API_BASE_URL", "http://localhost:8000")
    with pytest.raises(RuntimeError, match="Unsupported LLM_PROVIDER"):
        load_config()


def test_load_config_defaults(monkeypatch: pytest.MonkeyPatch) -> None:
    """Config loads with correct defaults when optional vars are absent."""
    monkeypatch.setenv("NOTES_INPUT_FOLDER_ID", "input-folder")
    monkeypatch.setenv("NOTES_PROCESSED_FOLDER_ID", "processed-folder")
    monkeypatch.setenv("KAIANO_API_BASE_URL", "http://localhost:8000")
    monkeypatch.delenv("LLM_PROVIDER", raising=False)
    monkeypatch.delenv("LLM_MODEL", raising=False)
    monkeypatch.delenv("LOGGING_LEVEL", raising=False)

    cfg = load_config()

    assert cfg.llm_provider == "anthropic"
    assert cfg.llm_model == "claude-sonnet-4-6"
    assert cfg.logging_level == "INFO"
    assert cfg.min_transcript_chars == 200


def test_load_config_openai_provider(monkeypatch: pytest.MonkeyPatch) -> None:
    """OpenAI provider resolves correct default model."""
    monkeypatch.setenv("NOTES_INPUT_FOLDER_ID", "input-folder")
    monkeypatch.setenv("NOTES_PROCESSED_FOLDER_ID", "processed-folder")
    monkeypatch.setenv("KAIANO_API_BASE_URL", "http://localhost:8000")
    monkeypatch.setenv("LLM_PROVIDER", "openai")
    monkeypatch.delenv("LLM_MODEL", raising=False)

    cfg = load_config()

    assert cfg.llm_provider == "openai"
    assert cfg.llm_model == "gpt-4.1-mini"


def test_load_config_custom_model(monkeypatch: pytest.MonkeyPatch) -> None:
    """LLM_MODEL override is respected."""
    monkeypatch.setenv("NOTES_INPUT_FOLDER_ID", "input-folder")
    monkeypatch.setenv("NOTES_PROCESSED_FOLDER_ID", "processed-folder")
    monkeypatch.setenv("KAIANO_API_BASE_URL", "http://localhost:8000")
    monkeypatch.setenv("LLM_PROVIDER", "anthropic")
    monkeypatch.setenv("LLM_MODEL", "claude-opus-4-6")

    cfg = load_config()

    assert cfg.llm_model == "claude-opus-4-6"
