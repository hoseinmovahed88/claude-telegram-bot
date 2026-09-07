"""Configuration for one or many bots.

Two sources, in order: a ``bots.toml`` describing several isolated bots, or a
``.env`` describing a single one. Each bot gets its own token, workspace and
Claude settings, and nothing is shared between them at runtime.
"""

from __future__ import annotations

import os
from dataclasses import dataclass, replace
from pathlib import Path
from typing import Any

import tomllib
from dotenv import load_dotenv

PERMISSION_MODES = (
    "default",
    "acceptEdits",
    "plan",
    "dontAsk",
    "bypassPermissions",
    "auto",
)

EFFORT_LEVELS = ("low", "medium", "high", "xhigh", "max")

DEFAULT_CONFIG_FILE = "bots.toml"

# Everything a [defaults] table may set, and a per-bot table may override.
_SHARED_KEYS = frozenset(
    {
        "allowed_user_ids",
        "model",
        "permission_mode",
        "effort",
        "allowed_tools",
        "disallowed_tools",
        "max_turns",
        "permission_timeout",
        "verbose",
    }
)
_BOT_ONLY_KEYS = frozenset({"token", "token_env", "workspace"})


class ConfigError(RuntimeError):
    """Raised when the configuration is missing or contradicts something required."""


def _csv(raw: str | None) -> tuple[str, ...]:
    if not raw:
        return ()
    return tuple(part.strip() for part in raw.split(",") if part.strip())


def _bool(raw: str | None, default: bool = False) -> bool:
    if raw is None or raw == "":
        return default
    return raw.strip().lower() in {"1", "true", "yes", "on"}


@dataclass(frozen=True)
class BotConfig:
    """Everything one bot needs. Two bots share no mutable state."""

    name: str
    bot_token: str
    allowed_user_ids: frozenset[int]
    workspace_root: Path
    model: str | None
    permission_mode: str
    effort: str | None
    allowed_tools: tuple[str, ...]
    disallowed_tools: tuple[str, ...]
    permission_timeout: float
    max_turns: int | None
    verbose_default: bool

    def redacted_token(self) -> str:
        head, _, _ = self.bot_token.partition(":")
        return f"{head}:***"


# ---------------------------------------------------------------- validation


def _check_mode(mode: str, where: str) -> str:
    if mode not in PERMISSION_MODES:
        raise ConfigError(
            f"{where}: permission_mode={mode!r} is not one of {', '.join(PERMISSION_MODES)}."
        )
    return mode


def _check_effort(effort: str | None, where: str) -> str | None:
    if effort is not None and effort not in EFFORT_LEVELS:
        raise ConfigError(
            f"{where}: effort={effort!r} is not one of {', '.join(EFFORT_LEVELS)}."
        )
    return effort


def _check_users(user_ids: Any, where: str) -> frozenset[int]:
    if not user_ids:
        raise ConfigError(
            f"{where}: allowed_user_ids is empty. A bot exposes a Claude Code session "
            "with file and shell access, so an explicit allow-list of numeric Telegram "
            "user IDs is mandatory."
        )
    try:
        return frozenset(int(value) for value in user_ids)
    except (TypeError, ValueError) as exc:
        raise ConfigError(f"{where}: allowed_user_ids must be numeric ({exc}).") from None


def _prepare_workspace(raw: Any, base: Path, where: str) -> Path:
    if not raw:
        raise ConfigError(f"{where}: workspace is required.")
    workspace = Path(str(raw)).expanduser()
    if not workspace.is_absolute():
        workspace = base / workspace
    try:
        workspace.mkdir(parents=True, exist_ok=True)
    except OSError as exc:
        raise ConfigError(f"{where}: cannot create workspace {workspace}: {exc}") from None
    return workspace.resolve()


# ------------------------------------------------------------------- loading


def load_configs(path: str | Path | None = None) -> list[BotConfig]:
    """Load every configured bot. Falls back to single-bot .env when no TOML exists."""
    load_dotenv()

    config_file = Path(path) if path else Path(os.getenv("BOTS_CONFIG") or DEFAULT_CONFIG_FILE)
    if config_file.is_file():
        return _from_toml(config_file)
    if path is not None:
        raise ConfigError(f"{config_file} does not exist.")
    return [_from_env()]


def _from_toml(config_file: Path) -> list[BotConfig]:
    try:
        document = tomllib.loads(config_file.read_text(encoding="utf-8"))
    except (OSError, tomllib.TOMLDecodeError) as exc:
        raise ConfigError(f"{config_file}: {exc}") from None

    defaults = document.get("defaults") or {}
    if unknown := set(defaults) - _SHARED_KEYS:
        raise ConfigError(
            f"{config_file} [defaults]: unknown key(s) {', '.join(sorted(unknown))}."
        )

    table = document.get("bots") or {}
    if not isinstance(table, dict) or not table:
        raise ConfigError(
            f"{config_file}: define at least one bot, e.g.\n\n"
            '  [bots.work]\n  token_env = "WORK_BOT_TOKEN"\n  workspace = "~/code/work"'
        )

    base = config_file.parent.resolve()
    configs = [
        _one_from_toml(name, settings, defaults, base, config_file)
        for name, settings in table.items()
    ]

    seen: dict[str, str] = {}
    for config in configs:
        if clash := seen.get(config.bot_token):
            raise ConfigError(
                f"{config_file}: bots {clash!r} and {config.name!r} share a token. "
                "Telegram allows only one poller per token, so they would steal each "
                "other's updates -- give every bot its own token from @BotFather."
            )
        seen[config.bot_token] = config.name
    return configs


def _one_from_toml(
    name: str, settings: Any, defaults: dict, base: Path, config_file: Path
) -> BotConfig:
    where = f"{config_file} [bots.{name}]"
    if not isinstance(settings, dict):
        raise ConfigError(f"{where}: expected a table of settings.")
    if unknown := set(settings) - _SHARED_KEYS - _BOT_ONLY_KEYS:
        raise ConfigError(f"{where}: unknown key(s) {', '.join(sorted(unknown))}.")

    merged = {**defaults, **settings}

    token = str(merged.get("token") or "").strip()
    token_env = str(merged.get("token_env") or "").strip()
    if token and token_env:
        raise ConfigError(f"{where}: set token or token_env, not both.")
    if token_env:
        token = (os.getenv(token_env) or "").strip()
        if not token:
            raise ConfigError(f"{where}: environment variable {token_env} is not set.")
    if not token:
        raise ConfigError(f"{where}: token or token_env is required.")

    return BotConfig(
        name=name,
        bot_token=token,
        allowed_user_ids=_check_users(merged.get("allowed_user_ids"), where),
        workspace_root=_prepare_workspace(merged.get("workspace"), base, where),
        model=(str(merged["model"]).strip() or None) if merged.get("model") else None,
        permission_mode=_check_mode(str(merged.get("permission_mode") or "default"), where),
        effort=_check_effort(
            (str(merged["effort"]).strip() or None) if merged.get("effort") else None, where
        ),
        allowed_tools=tuple(str(t) for t in merged.get("allowed_tools") or ()),
        disallowed_tools=tuple(str(t) for t in merged.get("disallowed_tools") or ()),
        permission_timeout=float(merged.get("permission_timeout", 300)),
        max_turns=int(merged["max_turns"]) if merged.get("max_turns") else None,
        verbose_default=bool(merged.get("verbose", False)),
    )


def _from_env() -> BotConfig:
    where = ".env"

    token = (os.getenv("TELEGRAM_BOT_TOKEN") or "").strip()
    if not token:
        raise ConfigError(
            "No bots.toml was found and TELEGRAM_BOT_TOKEN is not set. Configure a "
            "single bot in .env (see .env.example), or several in bots.toml "
            "(see bots.example.toml)."
        )

    raw_turns = (os.getenv("CLAUDE_MAX_TURNS") or "").strip()
    try:
        max_turns = int(raw_turns) if raw_turns else None
    except ValueError:
        raise ConfigError("CLAUDE_MAX_TURNS must be an integer if set.") from None

    try:
        timeout = float(os.getenv("PERMISSION_TIMEOUT") or 300)
    except ValueError:
        raise ConfigError("PERMISSION_TIMEOUT must be a number.") from None

    return BotConfig(
        name=(os.getenv("BOT_NAME") or "default").strip(),
        bot_token=token,
        allowed_user_ids=_check_users(_csv(os.getenv("TELEGRAM_ALLOWED_USER_IDS")), where),
        workspace_root=_prepare_workspace(
            os.getenv("CLAUDE_WORKSPACE") or "./workspace", Path.cwd(), where
        ),
        model=(os.getenv("CLAUDE_MODEL") or "").strip() or None,
        permission_mode=_check_mode(
            (os.getenv("CLAUDE_PERMISSION_MODE") or "default").strip(), where
        ),
        effort=_check_effort((os.getenv("CLAUDE_EFFORT") or "").strip() or None, where),
        allowed_tools=_csv(os.getenv("CLAUDE_ALLOWED_TOOLS")),
        disallowed_tools=_csv(os.getenv("CLAUDE_DISALLOWED_TOOLS")),
        permission_timeout=timeout,
        max_turns=max_turns,
        verbose_default=_bool(os.getenv("VERBOSE_DEFAULT")),
    )


def with_overrides(config: BotConfig, **changes: Any) -> BotConfig:
    """Test and tooling helper -- BotConfig is frozen."""
    return replace(config, **changes)
