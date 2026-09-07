from __future__ import annotations

import warnings

from claude_agent_sdk import CanUseToolShadowedWarning

from claude_telegram_bot import __main__ as entry
from claude_telegram_bot.config import ConfigError


def test_a_config_error_exits_without_starting_the_bot(monkeypatch, caplog):
    def boom():
        raise ConfigError("TELEGRAM_ALLOWED_USER_IDS is empty.")

    monkeypatch.setattr(entry.Config, "from_env", staticmethod(boom))
    started = []
    monkeypatch.setattr(entry, "build_application", lambda c: started.append(c))

    assert entry.main() == 2
    assert started == []
    assert "TELEGRAM_ALLOWED_USER_IDS" in caplog.text


def test_pre_approved_tools_silence_the_shadow_warning(monkeypatch, config):
    with_tools = type(config)(**{**config.__dict__, "allowed_tools": ("Read", "Glob")})
    monkeypatch.setattr(entry.Config, "from_env", staticmethod(lambda: with_tools))

    class FakeApp:
        bot_data: dict = {}

        def run_polling(self, **kwargs):
            pass

    monkeypatch.setattr(entry, "build_application", lambda c: FakeApp())

    with warnings.catch_warnings(record=True) as seen:
        warnings.simplefilter("always")
        assert entry.main() == 0
        warnings.warn("shadowed", CanUseToolShadowedWarning, stacklevel=1)

    assert not [w for w in seen if issubclass(w.category, CanUseToolShadowedWarning)]


def test_the_warning_is_left_alone_when_nothing_is_pre_approved(monkeypatch, config):
    monkeypatch.setattr(entry.Config, "from_env", staticmethod(lambda: config))

    class FakeApp:
        bot_data: dict = {}

        def run_polling(self, **kwargs):
            pass

    monkeypatch.setattr(entry, "build_application", lambda c: FakeApp())

    with warnings.catch_warnings(record=True) as seen:
        warnings.simplefilter("always")
        assert entry.main() == 0
        warnings.warn("shadowed", CanUseToolShadowedWarning, stacklevel=1)

    assert [w for w in seen if issubclass(w.category, CanUseToolShadowedWarning)]
