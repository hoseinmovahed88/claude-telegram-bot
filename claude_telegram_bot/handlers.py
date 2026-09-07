"""Telegram command and message handlers."""

from __future__ import annotations

import asyncio
import logging
from pathlib import Path

from claude_agent_sdk import list_sessions
from telegram import Update
from telegram.ext import (
    Application,
    ApplicationBuilder,
    CallbackQueryHandler,
    CommandHandler,
    ContextTypes,
    MessageHandler,
    filters,
)

from .config import PERMISSION_MODES, BotConfig
from .render import esc, truncate
from .session import SessionBusy, SessionManager
from .sink import TelegramSink

log = logging.getLogger(__name__)

HELP = """<b>Claude Code, in Telegram.</b>
Send any message and it becomes a prompt for a live Claude Code session. Every
tool call the agent makes shows up here as its own message, so you watch the
session as it happens.

<b>Session</b>
/new — end this session and start a fresh one
/resume <code>&lt;id&gt;</code> — continue an earlier session
/sessions — list recent sessions in the current directory
/stop — interrupt the turn that is running
/status — session id, directory, model, mode, spend
/context — context-window usage

<b>Settings</b>
/cd <code>&lt;path&gt;</code> — change the working directory (within the workspace)
/mode <code>&lt;mode&gt;</code> — {modes}
/model <code>&lt;name&gt;</code> — set the model (<code>-</code> for the default)
/verbose — toggle thinking and tool results
/allow — show or clear tools you granted "always allow"
"""


def _sink(update: Update, context: ContextTypes.DEFAULT_TYPE) -> TelegramSink:
    return TelegramSink(context.bot, update.effective_chat.id)


def _session(update: Update, context: ContextTypes.DEFAULT_TYPE):
    manager: SessionManager = context.application.bot_data["sessions"]
    return manager.get(update.effective_chat.id, context.bot)


async def unauthorised(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Log every attempt; answer only if the owner asked us to.

    Silence is the default: a reply confirms to a stranger that the bot is
    live and listening. The log line carries what the owner needs to add
    someone deliberately.
    """
    config: BotConfig = context.application.bot_data["config"]
    user = update.effective_user
    chat = update.effective_chat
    log.warning(
        "bot %r rejected user id=%s username=%s in chat id=%s type=%s",
        config.name,
        user.id if user else "?",
        user.username if user else "?",
        chat.id if chat else "?",
        chat.type if chat else "?",
    )
    if config.reply_to_strangers and update.effective_message:
        await update.effective_message.reply_text(
            "This bot is private. Your Telegram user id is "
            f"{user.id if user else 'unknown'}."
        )


async def wrong_chat(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """An allowed user, but somewhere the session must not be shown."""
    config: BotConfig = context.application.bot_data["config"]
    chat = update.effective_chat
    log.warning(
        "bot %r ignored an allowed user in chat id=%s type=%s",
        config.name,
        chat.id if chat else "?",
        chat.type if chat else "?",
    )
    if update.effective_message:
        await update.effective_message.reply_text(
            "Not here — everyone in this chat would see the session output. "
            "Message me privately instead. To use this chat anyway, add its id "
            f"({chat.id if chat else '?'}) to allowed_chat_ids."
        )


async def start(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    config: BotConfig = context.application.bot_data["config"]
    await update.effective_message.reply_html(
        HELP.format(modes=" | ".join(PERMISSION_MODES))
        + f"\n<i>bot <code>{esc(config.name)}</code> \N{MIDDLE DOT} workspace "
        f"<code>{esc(config.workspace_root)}</code></i>"
    )


async def status(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    session = _session(update, context)
    state = session.state
    always = ", ".join(sorted(session.broker.always_allow)) or "none"
    config: BotConfig = context.application.bot_data["config"]
    lines = [
        f"<b>Session</b> \N{MIDDLE DOT} bot <code>{esc(config.name)}</code>",
        f"id: <code>{esc(state.session_id or 'not started')}</code>",
        f"directory: <code>{esc(state.cwd)}</code>",
        f"model: <code>{esc(state.model or 'default')}</code>",
        f"mode: <code>{esc(state.permission_mode)}</code>",
        f"verbose: <code>{'on' if state.verbose else 'off'}</code>",
        f"running: <code>{'yes' if session.busy else 'no'}</code>",
        f"turns: <code>{state.turns}</code> \N{MIDDLE DOT} spend: <code>${state.total_cost_usd:.4f}</code>",
        f"always allowed: <code>{esc(always)}</code>",
    ]
    await update.effective_message.reply_html("\n".join(lines))


async def new_session(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    session = _session(update, context)
    if session.busy:
        await session.interrupt()
    await session.reset()
    await update.effective_message.reply_html(
        f"🆕 New session in <code>{esc(session.state.cwd)}</code>."
    )


async def resume(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    session = _session(update, context)
    if not context.args:
        await update.effective_message.reply_html(
            "Usage: <code>/resume &lt;session-id&gt;</code> — see /sessions."
        )
        return
    session_id = context.args[0].strip()
    if session.busy:
        await session.interrupt()
    await session.reset(resume=session_id)
    await update.effective_message.reply_html(
        f"⏪ Next message resumes <code>{esc(session_id)}</code>."
    )


async def sessions(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    session = _session(update, context)
    try:
        found = await asyncio.to_thread(
            list_sessions, directory=str(session.state.cwd), limit=10
        )
    except Exception as exc:  # the SDK raises OSError-ish variants per backend
        log.exception("list_sessions failed")
        await update.effective_message.reply_html(f"⚠️ {esc(exc)}")
        return

    if not found:
        await update.effective_message.reply_html(
            f"No stored sessions in <code>{esc(session.state.cwd)}</code>."
        )
        return

    lines = [f"<b>Recent sessions in</b> <code>{esc(session.state.cwd)}</code>"]
    for info in found:
        title = info.custom_title or info.summary or info.first_prompt or "(no summary)"
        lines.append(
            f"<code>{esc(info.session_id)}</code>\n  {esc(truncate(title, 90))}"
        )
    await update.effective_message.reply_html("\n".join(lines))


async def stop(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    manager: SessionManager = context.application.bot_data["sessions"]
    session = manager.peek(update.effective_chat.id)
    if session is None or not await session.interrupt():
        await update.effective_message.reply_html("<i>Nothing is running.</i>")
        return
    await update.effective_message.reply_html("🛑 Interrupted.")


async def change_dir(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    config: BotConfig = context.application.bot_data["config"]
    session = _session(update, context)
    if not context.args:
        await update.effective_message.reply_html(
            f"<code>{esc(session.state.cwd)}</code>\n"
            f"Usage: <code>/cd &lt;path&gt;</code> (relative to the workspace)."
        )
        return

    raw = " ".join(context.args)
    candidate = Path(raw).expanduser()
    if not candidate.is_absolute():
        candidate = session.state.cwd / candidate
    try:
        target = candidate.resolve()
    except OSError as exc:
        await update.effective_message.reply_html(f"⚠️ {esc(exc)}")
        return

    if not target.is_relative_to(config.workspace_root):
        await update.effective_message.reply_html(
            f"⛔️ <code>{esc(target)}</code> is outside the workspace "
            f"(<code>{esc(config.workspace_root)}</code>)."
        )
        return
    if not target.is_dir():
        await update.effective_message.reply_html(
            f"⚠️ <code>{esc(target)}</code> is not a directory."
        )
        return

    if session.busy:
        await session.interrupt()
    await session.reset(cwd=target)
    await update.effective_message.reply_html(
        f"📂 <code>{esc(target)}</code>\n<i>New session starts on your next message.</i>"
    )


async def mode(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    session = _session(update, context)
    if not context.args:
        await update.effective_message.reply_html(
            f"mode: <code>{esc(session.state.permission_mode)}</code>\n"
            f"Usage: <code>/mode {esc(' | '.join(PERMISSION_MODES))}</code>"
        )
        return
    requested = context.args[0].strip()
    if requested not in PERMISSION_MODES:
        await update.effective_message.reply_html(
            f"⚠️ Unknown mode. Pick one of: <code>{esc(', '.join(PERMISSION_MODES))}</code>"
        )
        return
    await session.set_permission_mode(requested)
    note = (
        "\n<i>Tool calls now run without asking you.</i>"
        if requested in ("bypassPermissions", "acceptEdits")
        else ""
    )
    await update.effective_message.reply_html(
        f"🔧 mode: <code>{esc(requested)}</code>{note}"
    )


async def model(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    session = _session(update, context)
    if not context.args:
        await update.effective_message.reply_html(
            f"model: <code>{esc(session.state.model or 'default')}</code>\n"
            "Usage: <code>/model &lt;name&gt;</code> or <code>/model -</code>"
        )
        return
    requested = context.args[0].strip()
    chosen = None if requested == "-" else requested
    await session.set_model(chosen)
    await update.effective_message.reply_html(
        f"🧠 model: <code>{esc(chosen or 'default')}</code>"
    )


async def verbose(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    session = _session(update, context)
    session.state.verbose = not session.state.verbose
    await update.effective_message.reply_html(
        f"🔍 verbose: <code>{'on' if session.state.verbose else 'off'}</code>\n"
        "<i>Shows thinking and tool results.</i>"
    )


async def allow(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    session = _session(update, context)
    if context.args and context.args[0].strip() in ("clear", "reset"):
        session.broker.always_allow.clear()
        await update.effective_message.reply_html("♾ Cleared the always-allow list.")
        return
    granted = ", ".join(sorted(session.broker.always_allow)) or "none"
    await update.effective_message.reply_html(
        f"♾ always allowed: <code>{esc(granted)}</code>\n"
        "<i>Use <code>/allow clear</code> to revoke.</i>"
    )


async def context_usage(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    session = _session(update, context)
    try:
        usage = await session.context_usage()
    except Exception as exc:
        log.exception("get_context_usage failed")
        await update.effective_message.reply_html(f"⚠️ {esc(exc)}")
        return
    if usage is None:
        await update.effective_message.reply_html("<i>No session is connected yet.</i>")
        return
    await update.effective_message.reply_html(
        f"<pre>{esc(truncate(str(usage), 1200))}</pre>"
    )


async def on_permission_button(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    query = update.callback_query
    config: BotConfig = context.application.bot_data["config"]
    user = update.effective_user
    if user is None or user.id not in config.allowed_user_ids:
        await query.answer("Not your session.", show_alert=True)
        return
    try:
        _, request_id, action = (query.data or "").split(":", 2)
    except ValueError:
        await query.answer("Malformed button.")
        return
    manager: SessionManager = context.application.bot_data["sessions"]
    label = manager.registry.dispatch(request_id, action)
    await query.answer(label or "That request already expired.")


async def on_message(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    message = update.effective_message
    prompt = (message.text or message.caption or "").strip()
    if not prompt:
        return

    session = _session(update, context)
    try:
        await session.run(prompt, _sink(update, context))
    except SessionBusy:
        await message.reply_html(
            "⏳ <i>The previous turn is still running. Wait for it, or /stop it.</i>"
        )


async def _shutdown(application: Application) -> None:
    manager: SessionManager = application.bot_data["sessions"]
    await manager.shutdown()


def authorised_filter(config: BotConfig) -> filters.BaseFilter:
    """Who, and where. Both halves matter.

    An allow-listed user is still refused in a group chat unless that chat is
    named explicitly, because the bot's replies carry file contents and command
    output to everyone who can read the chat. Edits are excluded too: editing
    an old message would silently re-run it as a new prompt.
    """
    who = filters.User(user_id=list(config.allowed_user_ids))
    where = (
        filters.Chat(chat_id=list(config.allowed_chat_ids))
        if config.allowed_chat_ids
        else filters.ChatType.PRIVATE
    )
    return who & where & ~filters.UpdateType.EDITED


def build_application(config: BotConfig) -> Application:
    only_allowed = filters.User(user_id=list(config.allowed_user_ids))
    authorised = authorised_filter(config)

    application = (
        ApplicationBuilder()
        .token(config.bot_token)
        .concurrent_updates(True)  # permission buttons must be handled mid-turn
        .post_shutdown(_shutdown)
        .build()
    )
    application.bot_data["config"] = config
    application.bot_data["sessions"] = SessionManager(config)

    commands = {
        "start": start,
        "help": start,
        "status": status,
        "new": new_session,
        "resume": resume,
        "sessions": sessions,
        "stop": stop,
        "cd": change_dir,
        "mode": mode,
        "model": model,
        "verbose": verbose,
        "allow": allow,
        "context": context_usage,
    }
    for name, callback in commands.items():
        application.add_handler(CommandHandler(name, callback, filters=authorised))

    application.add_handler(CallbackQueryHandler(on_permission_button, pattern=r"^p:"))
    application.add_handler(
        MessageHandler(filters.TEXT & ~filters.COMMAND & authorised, on_message)
    )
    # An allow-listed user, but in a chat the session must not be shown in.
    application.add_handler(MessageHandler(only_allowed & ~filters.UpdateType.EDITED, wrong_chat))
    # Everyone else.
    application.add_handler(MessageHandler(~only_allowed, unauthorised))

    return application
