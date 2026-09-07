"""Runs one or many bots side by side in a single process."""

from __future__ import annotations

import asyncio
import contextlib
import logging
import signal

from telegram.error import InvalidToken, TelegramError
from telegram.ext import Application

from .config import BotConfig
from .handlers import build_application
from .session import SessionManager

log = logging.getLogger(__name__)


class StartupError(RuntimeError):
    """A bot could not be started; the name of the offender is in the message."""


async def _start(application: Application, config: BotConfig) -> None:
    await application.initialize()
    me = await application.bot.get_me()
    await application.start()
    await application.updater.start_polling(
        drop_pending_updates=True,
        # Never even fetch the rest: no channel posts, no inline queries, no
        # chat-member events, and no edits (an edit must not re-run a prompt).
        allowed_updates=["message", "callback_query"],
    )
    where = (
        "chats " + ", ".join(str(i) for i in sorted(config.allowed_chat_ids))
        if config.allowed_chat_ids
        else "private chats only"
    )
    log.info(
        "bot %r polling as @%s | workspace=%s model=%s mode=%s",
        config.name,
        me.username,
        config.workspace_root,
        config.model or "default",
        config.permission_mode,
    )
    log.info(
        "bot %r is private to %d user id(s): %s | %s",
        config.name,
        len(config.allowed_user_ids),
        ", ".join(str(i) for i in sorted(config.allowed_user_ids)),
        where,
    )


async def _stop(application: Application, config: BotConfig) -> None:
    """Unwind one bot, closing its Claude sessions before its Telegram plumbing.

    Application.shutdown() does not run the post_shutdown hook -- only
    run_polling() does -- so the sessions are closed here explicitly.
    """
    async def step(what: str, coro) -> None:
        # One bot failing to unwind must not strand the others.
        try:
            await coro
        except Exception:
            log.exception("bot %r: %s failed during shutdown", config.name, what)

    manager: SessionManager | None = application.bot_data.get("sessions")
    if manager is not None:
        await step("closing sessions", manager.shutdown())
    if application.updater is not None and application.updater.running:
        await step("stopping the updater", application.updater.stop())
    if application.running:
        await step("stopping", application.stop())
    await step("shutdown", application.shutdown())
    log.info("bot %r stopped", config.name)


def _install_signal_handlers(stop: asyncio.Event) -> None:
    loop = asyncio.get_running_loop()
    for sig in (signal.SIGINT, signal.SIGTERM):
        with contextlib.suppress(NotImplementedError):  # e.g. Windows
            loop.add_signal_handler(sig, stop.set)


async def run_bots(configs: list[BotConfig], stop: asyncio.Event | None = None) -> None:
    """Start every bot, wait for a shutdown signal, then unwind all of them.

    Pass ``stop`` to drive shutdown yourself; otherwise SIGINT and SIGTERM set it.
    """
    applications = [(build_application(config), config) for config in configs]
    if stop is None:
        stop = asyncio.Event()
        _install_signal_handlers(stop)

    started: list[tuple[Application, BotConfig]] = []
    try:
        for application, config in applications:
            try:
                await _start(application, config)
            except InvalidToken as exc:
                raise StartupError(
                    f"bot {config.name!r}: Telegram rejected the token ({exc}). "
                    "Check it against @BotFather."
                ) from None
            except TelegramError as exc:
                raise StartupError(f"bot {config.name!r}: {exc}") from None
            started.append((application, config))

        log.info("%d bot(s) running; press Ctrl-C to stop", len(started))
        await stop.wait()
        log.info("shutting down")
    finally:
        for application, config in reversed(started):
            await _stop(application, config)
