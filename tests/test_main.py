from __future__ import annotations

import logging
import warnings

from claude_agent_sdk import CanUseToolShadowedWarning

from claude_telegram_bot import __main__ as entry
from claude_telegram_bot.config import ConfigError
from claude_telegram_bot.runner import StartupError


def _no_bots_started(monkeypatch):
    started: list = []
    monkeypatch.setattr(entry, "run_bots", lambda configs: started.append(configs))
    return started


def test_a_config_error_exits_without_starting_anything(monkeypatch, caplog):
    def boom(path=None):
        raise ConfigError("allowed_user_ids is empty.")

    monkeypatch.setattr(entry, "load_configs", boom)
    started = _no_bots_started(monkeypatch)

    assert entry.main([]) == 2
    assert started == []
    assert "allowed_user_ids" in caplog.text


def test_a_startup_error_is_reported_with_its_bot_name(monkeypatch, config, caplog):
    monkeypatch.setattr(entry, "load_configs", lambda path=None: [config])

    async def boom(configs):
        raise StartupError("bot 'work': Telegram rejected the token")

    monkeypatch.setattr(entry, "run_bots", boom)

    assert entry.main([]) == 3
    assert "work" in caplog.text


def test_the_config_path_argument_is_forwarded(monkeypatch, config):
    seen: list = []
    monkeypatch.setattr(entry, "load_configs", lambda path=None: seen.append(path) or [config])

    async def noop(configs):
        return None

    monkeypatch.setattr(entry, "run_bots", noop)

    assert entry.main(["/etc/bots.toml"]) == 0
    assert seen == ["/etc/bots.toml"]


def test_every_bots_token_is_redacted_in_the_startup_log(monkeypatch, config, caplog):
    secret = "1234:this-is-the-secret"
    loud = type(config)(**{**config.__dict__, "bot_token": secret})
    monkeypatch.setattr(entry, "load_configs", lambda path=None: [loud])

    async def noop(configs):
        return None

    monkeypatch.setattr(entry, "run_bots", noop)

    with caplog.at_level(logging.INFO):
        assert entry.main([]) == 0
    assert "1234:***" in caplog.text
    assert secret not in caplog.text


def test_pre_approved_tools_are_announced_once_and_the_warning_silenced(
    monkeypatch, config, caplog
):
    with_tools = type(config)(**{**config.__dict__, "allowed_tools": ("Read", "Glob")})
    monkeypatch.setattr(entry, "load_configs", lambda path=None: [with_tools])

    async def noop(configs):
        return None

    monkeypatch.setattr(entry, "run_bots", noop)

    with caplog.at_level(logging.INFO), warnings.catch_warnings(record=True) as seen:
        warnings.simplefilter("always")
        assert entry.main([]) == 0
        warnings.warn("shadowed", CanUseToolShadowedWarning, stacklevel=1)

    assert "Read, Glob" in caplog.text
    assert not [w for w in seen if issubclass(w.category, CanUseToolShadowedWarning)]


def test_the_warning_is_left_alone_when_nothing_is_pre_approved(monkeypatch, config):
    monkeypatch.setattr(entry, "load_configs", lambda path=None: [config])

    async def noop(configs):
        return None

    monkeypatch.setattr(entry, "run_bots", noop)

    with warnings.catch_warnings(record=True) as seen:
        warnings.simplefilter("always")
        assert entry.main([]) == 0
        warnings.warn("shadowed", CanUseToolShadowedWarning, stacklevel=1)

    assert [w for w in seen if issubclass(w.category, CanUseToolShadowedWarning)]
