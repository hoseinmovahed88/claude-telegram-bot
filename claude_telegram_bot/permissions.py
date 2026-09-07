"""Ask for tool permission in Telegram and block the agent until an answer arrives.

The SDK dispatches ``can_use_tool`` in its own task, so awaiting a human here
stalls only the tool call being asked about -- ``/stop`` still gets through.
"""

from __future__ import annotations

import asyncio
import logging
import secrets
from dataclasses import dataclass

from claude_agent_sdk import (
    PermissionResultAllow,
    PermissionResultDeny,
    ToolPermissionContext,
)
from telegram import InlineKeyboardButton, InlineKeyboardMarkup
from telegram.error import TelegramError

from .render import describe_tool_use, esc

log = logging.getLogger(__name__)

ALLOW = "a"
DENY = "d"
ALWAYS = "A"
STOP = "s"

_ACTION_LABELS = {
    ALLOW: "✅ allowed",
    DENY: "⛔️ denied",
    ALWAYS: "♾ always allowed",
    STOP: "🛑 denied and stopped",
}


@dataclass
class _Pending:
    future: asyncio.Future[str]
    tool_name: str
    message_id: int | None = None


class PermissionBroker:
    """Bridges the SDK's permission callback to Telegram inline buttons."""

    _registry: dict[str, PermissionBroker] = {}

    def __init__(self, bot, chat_id: int, *, timeout: float, workspace_root: str) -> None:
        self._bot = bot
        self._chat_id = chat_id
        self._timeout = timeout
        self._root = workspace_root
        self._pending: dict[str, _Pending] = {}
        self.always_allow: set[str] = set()

    # -- lookup used by the single global CallbackQueryHandler ---------------

    @classmethod
    def dispatch(cls, request_id: str, action: str) -> str | None:
        """Resolve a pending request. Returns a human label, or None if unknown/stale."""
        broker = cls._registry.get(request_id)
        if broker is None:
            return None
        return broker._resolve(request_id, action)

    def _resolve(self, request_id: str, action: str) -> str | None:
        pending = self._pending.get(request_id)
        if pending is None or pending.future.done():
            return None
        pending.future.set_result(action)
        return _ACTION_LABELS.get(action, action)

    def cancel_all(self, reason: str = "session stopped") -> None:
        """Deny everything still waiting -- used when a turn is interrupted or reset."""
        for request_id, pending in list(self._pending.items()):
            if not pending.future.done():
                pending.future.set_result(DENY)
            self._registry.pop(request_id, None)
        if reason:
            log.debug("cancelled pending permissions for chat %s: %s", self._chat_id, reason)

    # -- the SDK callback ----------------------------------------------------

    async def __call__(
        self,
        tool_name: str,
        tool_input: dict,
        context: ToolPermissionContext,
    ) -> PermissionResultAllow | PermissionResultDeny:
        if tool_name in self.always_allow:
            return PermissionResultAllow(updated_input=tool_input)

        request_id = secrets.token_hex(6)
        loop = asyncio.get_running_loop()
        pending = _Pending(future=loop.create_future(), tool_name=tool_name)
        self._pending[request_id] = pending
        self._registry[request_id] = self

        body = describe_tool_use(tool_name, tool_input, self._root)
        reason = context.decision_reason or context.description
        # The CLI often passes the path or command back as the reason; skip the echo.
        if reason and esc(reason) not in body:
            body += f"\n<i>{esc(reason)}</i>"
        prompt = f"🔐 <b>Permission requested</b>\n\n{body}"

        keyboard = InlineKeyboardMarkup(
            [
                [
                    InlineKeyboardButton("✅ Allow", callback_data=f"p:{request_id}:{ALLOW}"),
                    InlineKeyboardButton("⛔️ Deny", callback_data=f"p:{request_id}:{DENY}"),
                ],
                [
                    InlineKeyboardButton(
                        f"♾ Always allow {tool_name}",
                        callback_data=f"p:{request_id}:{ALWAYS}",
                    ),
                ],
                [
                    InlineKeyboardButton(
                        "🛑 Deny & stop", callback_data=f"p:{request_id}:{STOP}"
                    ),
                ],
            ]
        )

        try:
            message = await self._bot.send_message(
                chat_id=self._chat_id,
                text=prompt,
                parse_mode="HTML",
                reply_markup=keyboard,
            )
            pending.message_id = message.message_id
        except TelegramError:
            log.exception("could not send permission prompt; denying %s", tool_name)
            self._forget(request_id)
            return PermissionResultDeny(
                message="The bot could not reach Telegram to ask for permission.",
                interrupt=True,
            )

        try:
            action = await asyncio.wait_for(pending.future, timeout=self._timeout)
            timed_out = False
        except asyncio.TimeoutError:
            action = DENY
            timed_out = True
        finally:
            self._forget(request_id)

        await self._finalise(pending, prompt, action, timed_out)

        if action == ALWAYS:
            self.always_allow.add(tool_name)
        if action in (ALLOW, ALWAYS):
            return PermissionResultAllow(updated_input=tool_input)
        if timed_out:
            return PermissionResultDeny(
                message=(
                    f"No answer within {int(self._timeout)}s, so {tool_name} was not run."
                ),
                interrupt=True,
            )
        return PermissionResultDeny(
            message=f"The user denied {tool_name}.",
            interrupt=action == STOP,
        )

    def _forget(self, request_id: str) -> None:
        self._pending.pop(request_id, None)
        self._registry.pop(request_id, None)

    async def _finalise(
        self, pending: _Pending, prompt: str, action: str, timed_out: bool
    ) -> None:
        if pending.message_id is None:
            return
        label = "⏳ timed out — denied" if timed_out else _ACTION_LABELS.get(action, action)
        try:
            await self._bot.edit_message_text(
                chat_id=self._chat_id,
                message_id=pending.message_id,
                text=f"{prompt}\n\n<i>{label}</i>",
                parse_mode="HTML",
                reply_markup=None,
            )
        except TelegramError:
            log.debug("could not edit permission prompt %s", pending.message_id)
