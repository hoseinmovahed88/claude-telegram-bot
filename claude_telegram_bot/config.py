"""Environment-backed configuration for the bot."""

from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path

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


class ConfigError(RuntimeError):
    """Raised when the environment is missing or contradicts something required."""


def _csv(raw: str | None) -> tuple[str, ...]:
    if not raw:
        return ()
    return tuple(part.strip() for part in raw.split(",") if part.strip())


def _bool(raw: str | None, default: bool = False) -> bool:
    if raw is None or raw == "":
        return default
    return raw.strip().lower() in {"1", "true", "yes", "on"}


@dataclass(frozen=True)
class Config:
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

    @classmethod
    def from_env(cls) -> Config:
        load_dotenv()

        token = (os.getenv("TELEGRAM_BOT_TOKEN") or "").strip()
        if not token:
            raise ConfigError("TELEGRAM_BOT_TOKEN is not set (see .env.example).")

        raw_ids = _csv(os.getenv("TELEGRAM_ALLOWED_USER_IDS"))
        if not raw_ids:
            raise ConfigError(
                "TELEGRAM_ALLOWED_USER_IDS is empty. This bot exposes a Claude Code "
                "session with file and shell access, so an explicit allow-list of "
                "numeric Telegram user IDs is mandatory."
            )
        try:
            user_ids = frozenset(int(value) for value in raw_ids)
        except ValueError as exc:
            raise ConfigError(
                f"TELEGRAM_ALLOWED_USER_IDS must be numeric Telegram user IDs: {exc}"
            ) from None

        workspace = Path(os.getenv("CLAUDE_WORKSPACE") or "./workspace").expanduser()
        workspace.mkdir(parents=True, exist_ok=True)
        workspace = workspace.resolve()

        mode = (os.getenv("CLAUDE_PERMISSION_MODE") or "default").strip()
        if mode not in PERMISSION_MODES:
            raise ConfigError(
                f"CLAUDE_PERMISSION_MODE={mode!r} is not one of {', '.join(PERMISSION_MODES)}."
            )

        effort = (os.getenv("CLAUDE_EFFORT") or "").strip() or None
        if effort is not None and effort not in EFFORT_LEVELS:
            raise ConfigError(
                f"CLAUDE_EFFORT={effort!r} is not one of {', '.join(EFFORT_LEVELS)}."
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

        return cls(
            bot_token=token,
            allowed_user_ids=user_ids,
            workspace_root=workspace,
            model=(os.getenv("CLAUDE_MODEL") or "").strip() or None,
            permission_mode=mode,
            effort=effort,
            allowed_tools=_csv(os.getenv("CLAUDE_ALLOWED_TOOLS")),
            disallowed_tools=_csv(os.getenv("CLAUDE_DISALLOWED_TOOLS")),
            permission_timeout=timeout,
            max_turns=max_turns,
            verbose_default=_bool(os.getenv("VERBOSE_DEFAULT")),
        )
