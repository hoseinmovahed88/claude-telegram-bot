"""One Claude Code session per Telegram chat, plus the pump that renders it."""

from __future__ import annotations

import asyncio
import logging
import re
import time
from dataclasses import dataclass, field
from pathlib import Path

from claude_agent_sdk import (
    AssistantMessage,
    ClaudeAgentOptions,
    ClaudeSDKClient,
    ClaudeSDKError,
    ConversationResetMessage,
    RateLimitEvent,
    ResultMessage,
    ServerToolResultBlock,
    ServerToolUseBlock,
    SystemMessage,
    TextBlock,
    ThinkingBlock,
    ToolResultBlock,
    ToolUseBlock,
    UserMessage,
)

from .config import Config
from .permissions import PermissionBroker
from .render import (
    describe_tool_use,
    esc,
    render_result_footer,
    tool_result_text,
    truncate,
)
from .sink import TelegramSink

log = logging.getLogger(__name__)

SYSTEM_PROMPT_APPEND = """
You are reachable through a Telegram chat, so your text is read on a phone.
Keep replies short and skimmable; prefer a few sentences over long prose, and
quote only the lines of code that matter. The user already sees every tool call
you make as a separate message, so do not narrate what you are about to do.
""".strip()

TOOL_RESULT_PREVIEW = 700
THINKING_PREVIEW = 900


# e.g. "[ede_diagnostic] result_type=user ..." -- CLI internals, not for the chat.
_DIAGNOSTIC = re.compile(r"^\s*\[[a-z0-9_]+_diagnostic\]", re.IGNORECASE)


def _clock(epoch_seconds: int) -> str:
    return time.strftime("%H:%M UTC", time.gmtime(epoch_seconds))


@dataclass
class SessionState:
    """Everything the /status command reports and the commands mutate."""

    cwd: Path
    model: str | None
    permission_mode: str
    verbose: bool
    session_id: str | None = None
    resume_from: str | None = None
    turns: int = 0
    total_cost_usd: float = 0.0
    tools: list[str] = field(default_factory=list)


class SessionBusy(RuntimeError):
    """Raised when a prompt arrives while the previous one is still running."""


class ChatSession:
    """Owns the SDK client for a single chat and renders its output."""

    def __init__(self, chat_id: int, bot, config: Config) -> None:
        self.chat_id = chat_id
        self.config = config
        self.state = SessionState(
            cwd=config.workspace_root,
            model=config.model,
            permission_mode=config.permission_mode,
            verbose=config.verbose_default,
        )
        self.broker = PermissionBroker(
            bot,
            chat_id,
            timeout=config.permission_timeout,
            workspace_root=str(config.workspace_root),
        )
        self._client: ClaudeSDKClient | None = None
        self._turn_lock = asyncio.Lock()
        self._interrupted = False

    @property
    def busy(self) -> bool:
        return self._turn_lock.locked()

    @property
    def connected(self) -> bool:
        return self._client is not None

    # -- lifecycle -----------------------------------------------------------

    def _build_options(self) -> ClaudeAgentOptions:
        return ClaudeAgentOptions(
            cwd=str(self.state.cwd),
            model=self.state.model,
            permission_mode=self.state.permission_mode,
            effort=self.config.effort,
            allowed_tools=list(self.config.allowed_tools),
            disallowed_tools=list(self.config.disallowed_tools),
            max_turns=self.config.max_turns,
            resume=self.state.resume_from,
            can_use_tool=self.broker,
            setting_sources=["user", "project", "local"],
            system_prompt={
                "type": "preset",
                "preset": "claude_code",
                "append": SYSTEM_PROMPT_APPEND,
            },
            stderr=lambda line: log.debug("cli[%s]: %s", self.chat_id, line.rstrip()),
        )

    async def _ensure_client(self) -> ClaudeSDKClient:
        if self._client is None:
            client = ClaudeSDKClient(options=self._build_options())
            await client.connect()
            self._client = client
            self.state.resume_from = None
        return self._client

    async def close(self) -> None:
        self.broker.cancel_all("session closed")
        client, self._client = self._client, None
        if client is not None:
            try:
                await client.disconnect()
            except (ClaudeSDKError, OSError, RuntimeError) as exc:
                log.debug("disconnect for chat %s failed: %s", self.chat_id, exc)

    async def reset(self, *, cwd: Path | None = None, resume: str | None = None) -> None:
        """Drop the running session; the next prompt starts (or resumes) a fresh one."""
        await self.close()
        if cwd is not None:
            self.state.cwd = cwd
        self.state.session_id = None
        self.state.resume_from = resume
        self.state.turns = 0
        self.state.total_cost_usd = 0.0
        self.state.tools = []
        self.broker.always_allow.clear()

    async def interrupt(self) -> bool:
        self.broker.cancel_all("interrupted")
        if self._client is None or not self.busy:
            return False
        self._interrupted = True
        try:
            await self._client.interrupt()
        except (ClaudeSDKError, OSError, RuntimeError) as exc:
            log.warning("interrupt failed for chat %s: %s", self.chat_id, exc)
            return False
        return True

    async def set_permission_mode(self, mode: str) -> None:
        self.state.permission_mode = mode
        if self._client is not None:
            await self._client.set_permission_mode(mode)

    async def set_model(self, model: str | None) -> None:
        self.state.model = model
        if self._client is not None:
            await self._client.set_model(model)

    async def context_usage(self):
        if self._client is None:
            return None
        return await self._client.get_context_usage()

    # -- running a turn ------------------------------------------------------

    async def run(self, prompt: str, sink: TelegramSink) -> None:
        if self.busy:
            raise SessionBusy
        async with self._turn_lock:
            self._interrupted = False
            sink.start_typing()
            try:
                client = await self._ensure_client()
                await client.query(prompt)
                await self._pump(client, sink)
            except asyncio.CancelledError:
                raise
            except ClaudeSDKError as exc:
                await self._fail(sink, exc)
            except (OSError, RuntimeError) as exc:
                await self._fail(sink, exc)
            finally:
                await sink.stop_typing()

    async def _fail(self, sink: TelegramSink, exc: BaseException) -> None:
        log.exception("session error in chat %s", self.chat_id)
        await self.close()
        await sink.html(
            f"❌ <b>{esc(type(exc).__name__)}</b>\n<pre>{esc(truncate(str(exc), 800))}</pre>\n"
            "<i>The session was closed; send another message to start a new one.</i>"
        )

    async def _pump(self, client: ClaudeSDKClient, sink: TelegramSink) -> None:
        async for message in client.receive_response():
            if isinstance(message, SystemMessage):
                await self._on_system(message, sink)
            elif isinstance(message, AssistantMessage):
                await self._on_assistant(message, sink)
            elif isinstance(message, UserMessage):
                await self._on_user(message, sink)
            elif isinstance(message, ResultMessage):
                await self._on_result(message, sink)
            elif isinstance(message, RateLimitEvent):
                await self._on_rate_limit(message, sink)
            elif isinstance(message, ConversationResetMessage):
                await sink.notice("the conversation was reset by the agent")

    async def _on_rate_limit(self, message: RateLimitEvent, sink: TelegramSink) -> None:
        """The CLI reports quota on every turn; only say something when it bites."""
        info = getattr(message, "rate_limit_info", None)
        status = getattr(info, "status", None)
        if info is None or status in (None, "allowed"):
            return
        parts = [f"rate limit {status}"]
        if kind := getattr(info, "rate_limit_type", None):
            parts.append(str(kind))
        if resets_at := getattr(info, "resets_at", None):
            parts.append(f"resets at {_clock(resets_at)}")
        await sink.notice(" \N{MIDDLE DOT} ".join(parts))

    async def _on_system(self, message: SystemMessage, sink: TelegramSink) -> None:
        data = message.data or {}
        if session_id := data.get("session_id"):
            self.state.session_id = session_id
        if message.subtype == "init":
            tools = data.get("tools") or []
            if isinstance(tools, list):
                self.state.tools = [str(t) for t in tools]
            if self.state.verbose:
                await sink.notice(
                    f"session {self.state.session_id or '?'} \N{MIDDLE DOT} "
                    f"{len(self.state.tools)} tools \N{MIDDLE DOT} "
                    f"model {data.get('model', self.state.model or 'default')}"
                )
        elif message.subtype == "compact_boundary":
            await sink.notice("context was compacted")

    async def _on_assistant(self, message: AssistantMessage, sink: TelegramSink) -> None:
        if message.session_id:
            self.state.session_id = message.session_id
        # Output produced by a subagent, not the main loop.
        marker = "\N{DOWNWARDS ARROW WITH TIP RIGHTWARDS} " if message.parent_tool_use_id else ""

        for block in message.content:
            if isinstance(block, TextBlock):
                if block.text.strip():
                    await sink.text(block.text, prefix=marker)
            elif isinstance(block, ThinkingBlock):
                if self.state.verbose and block.thinking.strip():
                    await sink.html(
                        "\N{THOUGHT BALLOON} <i>"
                        + esc(truncate(block.thinking, THINKING_PREVIEW))
                        + "</i>"
                    )
            elif isinstance(block, (ToolUseBlock, ServerToolUseBlock)):
                await sink.html(
                    marker
                    + describe_tool_use(
                        block.name, block.input, str(self.state.cwd)
                    )
                )

        if message.error:
            await sink.html(f"⚠️ <b>API error:</b> {esc(message.error)}")

    async def _on_user(self, message: UserMessage, sink: TelegramSink) -> None:
        """User messages carry the tool results the agent just received."""
        if not isinstance(message.content, list):
            return
        for block in message.content:
            if not isinstance(block, (ToolResultBlock, ServerToolResultBlock)):
                continue
            is_error = bool(getattr(block, "is_error", False))
            if is_error and self._interrupted:
                continue
            if not (self.state.verbose or is_error):
                continue
            body = tool_result_text(getattr(block, "content", None)).strip()
            if not body:
                continue
            header = "⚠️ <b>tool error</b>" if is_error else "\N{LEFTWARDS ARROW} <i>result</i>"
            await sink.block(truncate(body, TOOL_RESULT_PREVIEW), header=header)

    async def _on_result(self, message: ResultMessage, sink: TelegramSink) -> None:
        if message.session_id:
            self.state.session_id = message.session_id
        self.state.turns += message.num_turns
        if message.total_cost_usd:
            self.state.total_cost_usd += message.total_cost_usd

        if message.is_error and message.result:
            await sink.html(f"⚠️ {esc(truncate(message.result, 800))}")
        for error in message.errors or []:
            if _DIAGNOSTIC.match(str(error)):
                log.debug("cli diagnostic for chat %s: %s", self.chat_id, error)
                continue
            await sink.html(f"⚠️ {esc(truncate(str(error), 400))}")

        await sink.html(
            render_result_footer(
                subtype=message.subtype,
                is_error=message.is_error,
                duration_ms=message.duration_ms,
                num_turns=message.num_turns,
                total_cost_usd=message.total_cost_usd,
                terminal_reason=message.terminal_reason,
            )
        )


class SessionManager:
    """Keeps one ChatSession per chat id."""

    def __init__(self, config: Config) -> None:
        self.config = config
        self._sessions: dict[int, ChatSession] = {}

    def get(self, chat_id: int, bot) -> ChatSession:
        session = self._sessions.get(chat_id)
        if session is None:
            session = ChatSession(chat_id, bot, self.config)
            self._sessions[chat_id] = session
        return session

    def peek(self, chat_id: int) -> ChatSession | None:
        return self._sessions.get(chat_id)

    async def shutdown(self) -> None:
        await asyncio.gather(
            *(session.close() for session in self._sessions.values()),
            return_exceptions=True,
        )
        self._sessions.clear()
