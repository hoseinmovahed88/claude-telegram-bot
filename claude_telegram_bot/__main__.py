"""Entry point: python -m claude_telegram_bot"""

from __future__ import annotations

import logging
import sys
import warnings

from claude_agent_sdk import CanUseToolShadowedWarning

from .config import Config, ConfigError
from .handlers import build_application


def _configure_logging() -> None:
    logging.basicConfig(
        format="%(asctime)s %(levelname)-8s %(name)s | %(message)s",
        level=logging.INFO,
    )
    # httpx logs every getUpdates poll at INFO.
    logging.getLogger("httpx").setLevel(logging.WARNING)


def main() -> int:
    _configure_logging()
    log = logging.getLogger("claude_telegram_bot")

    try:
        config = Config.from_env()
    except ConfigError as exc:
        log.error("%s", exc)
        return 2

    if config.allowed_tools:
        # Pre-approved tools bypass the Telegram prompt by design; the SDK warns
        # about it on every reconnect, so state it once instead.
        warnings.filterwarnings("ignore", category=CanUseToolShadowedWarning)
        log.info(
            "CLAUDE_ALLOWED_TOOLS pre-approves %s -- these run without asking you",
            ", ".join(config.allowed_tools),
        )

    application = build_application(config)

    log.info(
        "workspace=%s model=%s mode=%s allowed_users=%d",
        config.workspace_root,
        config.model or "default",
        config.permission_mode,
        len(config.allowed_user_ids),
    )
    application.run_polling(drop_pending_updates=True)
    return 0


if __name__ == "__main__":
    sys.exit(main())
