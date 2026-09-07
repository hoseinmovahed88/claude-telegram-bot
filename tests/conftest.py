from __future__ import annotations

import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from claude_telegram_bot.config import Config  # noqa: E402


@dataclass
class SentMessage:
    chat_id: int
    text: str
    kwargs: dict[str, Any]
    message_id: int


class FakeBot:
    """Records what the bot would have sent, and hands back editable messages."""

    def __init__(self) -> None:
        self.sent: list[SentMessage] = []
        self.edits: list[tuple[int, str]] = []
        self.actions: list[str] = []
        self._next_id = 1

    async def send_message(self, chat_id: int, text: str, **kwargs: Any) -> SentMessage:
        message = SentMessage(chat_id, text, kwargs, self._next_id)
        self._next_id += 1
        self.sent.append(message)
        return message

    async def edit_message_text(self, chat_id: int, message_id: int, text: str, **kw: Any):
        self.edits.append((message_id, text))

    async def send_chat_action(self, chat_id: int, action: str) -> None:
        self.actions.append(action)

    @property
    def texts(self) -> list[str]:
        return [m.text for m in self.sent]

    def find(self, needle: str) -> list[str]:
        return [t for t in self.texts if needle in t]


@pytest.fixture
def bot() -> FakeBot:
    return FakeBot()


@pytest.fixture
def config(tmp_path: Path) -> Config:
    return Config(
        bot_token="1:test",
        allowed_user_ids=frozenset({42}),
        workspace_root=tmp_path,
        model=None,
        permission_mode="default",
        effort=None,
        allowed_tools=(),
        disallowed_tools=(),
        permission_timeout=0.3,
        max_turns=None,
        verbose_default=False,
    )


class FakeClient:
    """Stands in for ClaudeSDKClient, replaying a canned message list."""

    def __init__(self, messages: list[Any]) -> None:
        self._messages = messages
        self.interrupted = False
        self.prompts: list[str] = []

    async def connect(self) -> None:
        pass

    async def disconnect(self) -> None:
        pass

    async def query(self, prompt: str, session_id: str = "default") -> None:
        self.prompts.append(prompt)

    async def interrupt(self) -> None:
        self.interrupted = True

    async def receive_response(self):
        for message in self._messages:
            yield message
