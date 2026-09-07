from __future__ import annotations

import pytest

from claude_telegram_bot.config import Config, ConfigError


@pytest.fixture(autouse=True)
def clean_env(monkeypatch, tmp_path):
    for key in (
        "TELEGRAM_BOT_TOKEN",
        "TELEGRAM_ALLOWED_USER_IDS",
        "CLAUDE_WORKSPACE",
        "CLAUDE_MODEL",
        "CLAUDE_PERMISSION_MODE",
        "CLAUDE_EFFORT",
        "CLAUDE_ALLOWED_TOOLS",
        "CLAUDE_DISALLOWED_TOOLS",
        "CLAUDE_MAX_TURNS",
        "PERMISSION_TIMEOUT",
        "VERBOSE_DEFAULT",
    ):
        monkeypatch.delenv(key, raising=False)
    monkeypatch.setattr("claude_telegram_bot.config.load_dotenv", lambda *a, **k: None)
    monkeypatch.setenv("CLAUDE_WORKSPACE", str(tmp_path / "ws"))


def test_missing_token_is_rejected():
    with pytest.raises(ConfigError, match="TELEGRAM_BOT_TOKEN"):
        Config.from_env()


def test_empty_allowlist_is_rejected(monkeypatch):
    monkeypatch.setenv("TELEGRAM_BOT_TOKEN", "1:x")
    with pytest.raises(ConfigError, match="TELEGRAM_ALLOWED_USER_IDS"):
        Config.from_env()


def test_non_numeric_allowlist_is_rejected(monkeypatch):
    monkeypatch.setenv("TELEGRAM_BOT_TOKEN", "1:x")
    monkeypatch.setenv("TELEGRAM_ALLOWED_USER_IDS", "alice")
    with pytest.raises(ConfigError, match="numeric"):
        Config.from_env()


def test_unknown_permission_mode_is_rejected(monkeypatch):
    monkeypatch.setenv("TELEGRAM_BOT_TOKEN", "1:x")
    monkeypatch.setenv("TELEGRAM_ALLOWED_USER_IDS", "42")
    monkeypatch.setenv("CLAUDE_PERMISSION_MODE", "yolo")
    with pytest.raises(ConfigError, match="CLAUDE_PERMISSION_MODE"):
        Config.from_env()


def test_unknown_effort_is_rejected(monkeypatch):
    monkeypatch.setenv("TELEGRAM_BOT_TOKEN", "1:x")
    monkeypatch.setenv("TELEGRAM_ALLOWED_USER_IDS", "42")
    monkeypatch.setenv("CLAUDE_EFFORT", "turbo")
    with pytest.raises(ConfigError, match="CLAUDE_EFFORT"):
        Config.from_env()


def test_workspace_is_created_and_resolved(monkeypatch, tmp_path):
    monkeypatch.setenv("TELEGRAM_BOT_TOKEN", "1:x")
    monkeypatch.setenv("TELEGRAM_ALLOWED_USER_IDS", " 42 , 43 ")
    monkeypatch.setenv("CLAUDE_ALLOWED_TOOLS", "Read, Glob ,")
    config = Config.from_env()

    assert config.allowed_user_ids == frozenset({42, 43})
    assert config.allowed_tools == ("Read", "Glob")
    assert config.workspace_root.is_dir()
    assert config.workspace_root.is_absolute()
