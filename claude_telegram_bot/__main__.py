"""Entry point: python -m claude_telegram_bot"""

from __future__ import annotations

import asyncio
import logging
import sys
import warnings

from claude_agent_sdk import CanUseToolShadowedWarning

from .config import BotConfig, ConfigError, load_configs
from .runner import StartupError, run_bots


def _configure_logging() -> None:
    logging.basicConfig(
        format="%(asctime)s %(levelname)-8s %(name)s | %(message)s",
        level=logging.INFO,
    )
    # httpx logs every getUpdates poll at INFO.
    logging.getLogger("httpx").setLevel(logging.WARNING)


def _announce_pre_approved(configs: list[BotConfig], log: logging.Logger) -> None:
    """Pre-approved tools bypass the Telegram prompt by design; the SDK warns
    about it on every reconnect, so state it once per bot instead."""
    noisy = [config for config in configs if config.allowed_tools]
    if not noisy:
        return
    warnings.filterwarnings("ignore", category=CanUseToolShadowedWarning)
    for config in noisy:
        log.info(
            "bot %r pre-approves %s -- these run without asking you",
            config.name,
            ", ".join(config.allowed_tools),
        )


def main(argv: list[str] | None = None) -> int:
    _configure_logging()
    log = logging.getLogger("claude_telegram_bot")

    args = sys.argv[1:] if argv is None else argv
    config_path = args[0] if args else None

    try:
        configs = load_configs(config_path)
    except ConfigError as exc:
        log.error("%s", exc)
        return 2

    _announce_pre_approved(configs, log)
    log.info(
        "starting %d bot(s): %s",
        len(configs),
        ", ".join(f"{c.name} ({c.redacted_token()})" for c in configs),
    )

    try:
        asyncio.run(run_bots(configs))
    except StartupError as exc:
        log.error("%s", exc)
        return 3
    except KeyboardInterrupt:
        pass
    return 0


if __name__ == "__main__":
    sys.exit(main())
