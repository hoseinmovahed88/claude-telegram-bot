"""Streams a running session's output into a Telegram chat."""

from __future__ import annotations

import asyncio
import contextlib
import logging

from telegram.constants import ChatAction
from telegram.error import TelegramError

from .render import chunk, esc

log = logging.getLogger(__name__)

# Telegram clears the typing bubble after ~5s.
_TYPING_REFRESH = 4.0


class TelegramSink:
    """Sends session events to one chat, chunked and HTML-safe."""

    def __init__(self, bot, chat_id: int) -> None:
        self._bot = bot
        self._chat_id = chat_id
        self._typing: asyncio.Task | None = None

    async def html(self, text: str) -> None:
        """Send pre-formatted HTML. Falls back to plain text if Telegram rejects it."""
        if not text.strip():
            return
        try:
            await self._bot.send_message(
                chat_id=self._chat_id,
                text=text,
                parse_mode="HTML",
                disable_web_page_preview=True,
            )
        except TelegramError as exc:
            log.warning("HTML send failed (%s); retrying as plain text", exc)
            with contextlib.suppress(TelegramError):
                await self._bot.send_message(
                    chat_id=self._chat_id,
                    text=text[:4000],
                    disable_web_page_preview=True,
                )

    async def text(self, body: str, *, prefix: str = "") -> None:
        """Send plain text, escaped and split across as many messages as needed."""
        pieces = chunk(body)
        for index, piece in enumerate(pieces):
            head = prefix if index == 0 else ""
            await self.html(f"{head}{esc(piece)}")

    async def block(self, body: str, *, header: str = "") -> None:
        """Send text inside <pre>, escaped and chunked."""
        for index, piece in enumerate(chunk(body, 3000)):
            head = f"{header}\n" if header and index == 0 else ""
            await self.html(f"{head}<pre>{esc(piece)}</pre>")

    async def notice(self, body: str) -> None:
        await self.html(f"<i>{esc(body)}</i>")

    # -- typing indicator ----------------------------------------------------

    def start_typing(self) -> None:
        if self._typing is None or self._typing.done():
            self._typing = asyncio.create_task(self._keep_typing())

    async def stop_typing(self) -> None:
        task, self._typing = self._typing, None
        if task is not None:
            task.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await task

    async def _keep_typing(self) -> None:
        try:
            while True:
                with contextlib.suppress(TelegramError):
                    await self._bot.send_chat_action(self._chat_id, ChatAction.TYPING)
                await asyncio.sleep(_TYPING_REFRESH)
        except asyncio.CancelledError:
            raise
