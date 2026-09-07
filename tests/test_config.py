from __future__ import annotations

import pytest

from claude_telegram_bot.config import ConfigError, load_configs

ENV_KEYS = (
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
    "BOT_NAME",
    "BOTS_CONFIG",
)


@pytest.fixture(autouse=True)
def clean_env(monkeypatch, tmp_path):
    for key in ENV_KEYS:
        monkeypatch.delenv(key, raising=False)
    monkeypatch.setattr("claude_telegram_bot.config.load_dotenv", lambda *a, **k: None)
    monkeypatch.chdir(tmp_path)


def _toml(tmp_path, body: str):
    path = tmp_path / "bots.toml"
    path.write_text(body, encoding="utf-8")
    return path


# ------------------------------------------------------------------ .env mode


def test_env_mode_needs_a_token():
    with pytest.raises(ConfigError, match="TELEGRAM_BOT_TOKEN"):
        load_configs()


def test_env_mode_needs_an_allow_list(monkeypatch):
    monkeypatch.setenv("TELEGRAM_BOT_TOKEN", "1:x")
    with pytest.raises(ConfigError, match="allowed_user_ids is empty"):
        load_configs()


def test_env_mode_builds_one_bot(monkeypatch, tmp_path):
    monkeypatch.setenv("TELEGRAM_BOT_TOKEN", "1:x")
    monkeypatch.setenv("TELEGRAM_ALLOWED_USER_IDS", " 42 , 43 ")
    monkeypatch.setenv("CLAUDE_ALLOWED_TOOLS", "Read, Glob ,")

    (config,) = load_configs()

    assert config.name == "default"
    assert config.allowed_user_ids == frozenset({42, 43})
    assert config.allowed_tools == ("Read", "Glob")
    assert config.workspace_root.is_dir()


def test_env_mode_rejects_a_bad_mode(monkeypatch):
    monkeypatch.setenv("TELEGRAM_BOT_TOKEN", "1:x")
    monkeypatch.setenv("TELEGRAM_ALLOWED_USER_IDS", "42")
    monkeypatch.setenv("CLAUDE_PERMISSION_MODE", "yolo")
    with pytest.raises(ConfigError, match="permission_mode"):
        load_configs()


# ------------------------------------------------------------------ toml mode


def test_toml_is_preferred_over_env(monkeypatch, tmp_path):
    monkeypatch.setenv("TELEGRAM_BOT_TOKEN", "from-env:x")
    monkeypatch.setenv("TELEGRAM_ALLOWED_USER_IDS", "42")
    _toml(
        tmp_path,
        """
        [defaults]
        allowed_user_ids = [42]

        [bots.work]
        token = "111:aaa"
        workspace = "work"
        """,
    )
    (config,) = load_configs()
    assert config.name == "work"
    assert config.bot_token == "111:aaa"


def test_each_bot_gets_its_own_workspace_and_settings(tmp_path):
    _toml(
        tmp_path,
        """
        [defaults]
        allowed_user_ids = [42]
        permission_mode = "default"
        model = "claude-opus-5"

        [bots.work]
        token = "111:aaa"
        workspace = "code/work"

        [bots.blog]
        token = "222:bbb"
        workspace = "writing"
        permission_mode = "acceptEdits"
        model = "claude-sonnet-5"
        verbose = true
        """,
    )
    work, blog = load_configs()

    assert (work.name, blog.name) == ("work", "blog")
    assert work.workspace_root != blog.workspace_root
    assert work.workspace_root.is_dir() and blog.workspace_root.is_dir()
    # defaults flow down...
    assert work.permission_mode == "default"
    assert work.model == "claude-opus-5"
    assert work.allowed_user_ids == frozenset({42})
    # ...and per-bot keys win.
    assert blog.permission_mode == "acceptEdits"
    assert blog.model == "claude-sonnet-5"
    assert blog.verbose_default is True


def test_a_token_can_come_from_the_environment(monkeypatch, tmp_path):
    monkeypatch.setenv("WORK_BOT_TOKEN", "999:secret")
    _toml(
        tmp_path,
        """
        [defaults]
        allowed_user_ids = [42]

        [bots.work]
        token_env = "WORK_BOT_TOKEN"
        workspace = "w"
        """,
    )
    (config,) = load_configs()
    assert config.bot_token == "999:secret"


def test_a_missing_token_env_var_is_named(tmp_path):
    _toml(
        tmp_path,
        """
        [defaults]
        allowed_user_ids = [42]

        [bots.work]
        token_env = "NOT_SET_ANYWHERE"
        workspace = "w"
        """,
    )
    with pytest.raises(ConfigError, match="NOT_SET_ANYWHERE"):
        load_configs()


def test_two_bots_may_not_share_a_token(tmp_path):
    _toml(
        tmp_path,
        """
        [defaults]
        allowed_user_ids = [42]

        [bots.work]
        token = "111:aaa"
        workspace = "a"

        [bots.blog]
        token = "111:aaa"
        workspace = "b"
        """,
    )
    with pytest.raises(ConfigError, match="share a token"):
        load_configs()


def test_a_bot_without_a_workspace_is_rejected(tmp_path):
    _toml(
        tmp_path,
        """
        [defaults]
        allowed_user_ids = [42]

        [bots.work]
        token = "111:aaa"
        """,
    )
    with pytest.raises(ConfigError, match="workspace is required"):
        load_configs()


def test_a_bot_without_an_allow_list_is_rejected(tmp_path):
    _toml(
        tmp_path,
        """
        [bots.work]
        token = "111:aaa"
        workspace = "w"
        """,
    )
    with pytest.raises(ConfigError, match="allowed_user_ids is empty"):
        load_configs()


def test_a_typo_in_a_key_is_reported_not_ignored(tmp_path):
    _toml(
        tmp_path,
        """
        [defaults]
        allowed_user_ids = [42]

        [bots.work]
        token = "111:aaa"
        workspace = "w"
        permision_mode = "acceptEdits"
        """,
    )
    with pytest.raises(ConfigError, match="permision_mode"):
        load_configs()


def test_a_typo_in_defaults_is_reported(tmp_path):
    _toml(tmp_path, '[defaults]\nallowed_users = [42]\n')
    with pytest.raises(ConfigError, match="allowed_users"):
        load_configs()


def test_an_empty_bots_table_is_rejected(tmp_path):
    _toml(tmp_path, "[defaults]\nallowed_user_ids = [42]\n")
    with pytest.raises(ConfigError, match="at least one bot"):
        load_configs()


def test_broken_toml_names_the_file(tmp_path):
    _toml(tmp_path, "[bots.work\ntoken =")
    with pytest.raises(ConfigError, match="bots.toml"):
        load_configs()


def test_relative_workspaces_resolve_against_the_config_file(tmp_path):
    nested = tmp_path / "conf"
    nested.mkdir()
    path = nested / "bots.toml"
    path.write_text(
        '[defaults]\nallowed_user_ids = [42]\n\n'
        '[bots.work]\ntoken = "1:a"\nworkspace = "ws"\n',
        encoding="utf-8",
    )
    (config,) = load_configs(path)
    assert config.workspace_root == (nested / "ws").resolve()


def test_an_explicit_missing_path_is_an_error(tmp_path):
    with pytest.raises(ConfigError, match="does not exist"):
        load_configs(tmp_path / "nope.toml")


def test_the_token_is_redacted_for_logs(tmp_path):
    _toml(
        tmp_path,
        '[defaults]\nallowed_user_ids = [42]\n\n'
        '[bots.work]\ntoken = "111:supersecret"\nworkspace = "w"\n',
    )
    (config,) = load_configs()
    assert config.redacted_token() == "111:***"
    assert "supersecret" not in config.redacted_token()


# ------------------------------------------------------------- access scoping


def test_private_chats_only_is_the_default(tmp_path):
    _toml(
        tmp_path,
        '[defaults]\nallowed_user_ids = [42]\n\n'
        '[bots.work]\ntoken = "1:a"\nworkspace = "w"\n',
    )
    (config,) = load_configs()
    assert config.allowed_chat_ids == frozenset()
    assert config.reply_to_strangers is False


def test_chats_can_be_opted_in_per_bot(tmp_path):
    _toml(
        tmp_path,
        """
        [defaults]
        allowed_user_ids = [42]

        [bots.work]
        token = "1:a"
        workspace = "w"
        allowed_chat_ids = [-100123]
        reply_to_strangers = true

        [bots.blog]
        token = "2:b"
        workspace = "b"
        """,
    )
    work, blog = load_configs()
    assert work.allowed_chat_ids == frozenset({-100123})
    assert work.reply_to_strangers is True
    # Not inherited sideways from another bot.
    assert blog.allowed_chat_ids == frozenset()
    assert blog.reply_to_strangers is False


def test_non_numeric_chat_ids_are_rejected(tmp_path):
    _toml(
        tmp_path,
        '[defaults]\nallowed_user_ids = [42]\n\n'
        '[bots.work]\ntoken = "1:a"\nworkspace = "w"\nallowed_chat_ids = ["mygroup"]\n',
    )
    with pytest.raises(ConfigError, match="allowed_chat_ids"):
        load_configs()


def test_env_mode_supports_the_same_scoping(monkeypatch):
    monkeypatch.setenv("TELEGRAM_BOT_TOKEN", "1:x")
    monkeypatch.setenv("TELEGRAM_ALLOWED_USER_IDS", "42")
    monkeypatch.setenv("TELEGRAM_ALLOWED_CHAT_IDS", "-100123, -100456")
    monkeypatch.setenv("TELEGRAM_REPLY_TO_STRANGERS", "1")

    (config,) = load_configs()

    assert config.allowed_chat_ids == frozenset({-100123, -100456})
    assert config.reply_to_strangers is True
