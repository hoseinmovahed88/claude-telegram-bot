from __future__ import annotations

import pytest
from claude_agent_sdk import (
    AssistantMessage,
    ResultMessage,
    SystemMessage,
    TextBlock,
    ThinkingBlock,
    ToolResultBlock,
    ToolUseBlock,
    UserMessage,
)
from conftest import FakeClient

from claude_telegram_bot.session import ChatSession, SessionBusy
from claude_telegram_bot.sink import TelegramSink


def _transcript():
    return [
        SystemMessage(subtype="init", data={"session_id": "sess-1", "tools": ["Bash", "Read"]}),
        AssistantMessage(
            content=[TextBlock(text="Looking at the tree.")],
            model="claude-opus-5",
            parent_tool_use_id=None,
            error=None,
            usage=None,
            message_id="m1",
            stop_reason=None,
            session_id="sess-1",
            uuid="u1",
        ),
        AssistantMessage(
            content=[
                ThinkingBlock(thinking="secret reasoning", signature="sig"),
                ToolUseBlock(id="t1", name="Bash", input={"command": "ls -a"}),
            ],
            model="claude-opus-5",
            parent_tool_use_id=None,
            error=None,
            usage=None,
            message_id="m2",
            stop_reason="tool_use",
            session_id="sess-1",
            uuid="u2",
        ),
        UserMessage(
            content=[ToolResultBlock(tool_use_id="t1", content="a\nb", is_error=False)],
            uuid="u3",
            parent_tool_use_id=None,
            tool_use_result=None,
            origin=None,
        ),
        ResultMessage(
            subtype="success",
            duration_ms=1500,
            duration_api_ms=1200,
            is_error=False,
            num_turns=2,
            session_id="sess-1",
            stop_reason="end_turn",
            total_cost_usd=0.01,
            usage=None,
            result="done",
            structured_output=None,
            model_usage=None,
            permission_denials=None,
            deferred_tool_use=None,
            errors=None,
            api_error_status=None,
            uuid="u4",
            terminal_reason="end_turn",
        ),
    ]


async def _run(session: ChatSession, bot, messages) -> None:
    client = FakeClient(messages)
    session._client = client
    await session.run("what is here?", TelegramSink(bot, 1))


async def test_quiet_mode_shows_text_and_tools_but_not_internals(bot, config):
    session = ChatSession(1, bot, config)
    await _run(session, bot, _transcript())

    assert bot.find("Looking at the tree.")
    assert bot.find("<b>Bash</b>")
    assert bot.find("ls -a")
    # Thinking and successful tool results are hidden unless /verbose is on.
    assert not bot.find("secret reasoning")
    assert not bot.find("&lt;result&gt;") and not bot.find("result</i>")


async def test_verbose_mode_shows_thinking_and_results(bot, config):
    session = ChatSession(1, bot, config)
    session.state.verbose = True
    await _run(session, bot, _transcript())

    assert bot.find("secret reasoning")
    assert bot.find("result")
    assert bot.find("sess-1")


async def test_state_tracks_session_cost_and_turns(bot, config):
    session = ChatSession(1, bot, config)
    await _run(session, bot, _transcript())

    assert session.state.session_id == "sess-1"
    assert session.state.turns == 2
    assert session.state.total_cost_usd == pytest.approx(0.01)
    assert session.state.tools == ["Bash", "Read"]
    assert bot.find("$0.0100")


async def test_tool_errors_surface_even_when_quiet(bot, config):
    session = ChatSession(1, bot, config)
    messages = [
        UserMessage(
            content=[ToolResultBlock(tool_use_id="t1", content="boom", is_error=True)],
            uuid="u1",
            parent_tool_use_id=None,
            tool_use_result=None,
            origin=None,
        ),
    ]
    await _run(session, bot, messages)
    assert bot.find("tool error")
    assert bot.find("boom")


async def test_subagent_output_is_marked(bot, config):
    session = ChatSession(1, bot, config)
    messages = [
        AssistantMessage(
            content=[TextBlock(text="from the subagent")],
            model="claude-opus-5",
            parent_tool_use_id="task-1",
            error=None,
            usage=None,
            message_id="m1",
            stop_reason=None,
            session_id="sess-1",
            uuid="u1",
        ),
    ]
    await _run(session, bot, messages)
    assert bot.find("\N{DOWNWARDS ARROW WITH TIP RIGHTWARDS}")


async def test_long_output_is_split_across_messages(bot, config):
    session = ChatSession(1, bot, config)
    messages = [
        AssistantMessage(
            content=[TextBlock(text="\n".join(f"row {i}" for i in range(2000)))],
            model="claude-opus-5",
            parent_tool_use_id=None,
            error=None,
            usage=None,
            message_id="m1",
            stop_reason=None,
            session_id="sess-1",
            uuid="u1",
        ),
    ]
    await _run(session, bot, messages)
    assert len(bot.sent) > 1
    assert all(len(m.text) <= 4096 for m in bot.sent)


async def test_second_prompt_during_a_turn_is_rejected(bot, config):
    session = ChatSession(1, bot, config)
    async with session._turn_lock:
        with pytest.raises(SessionBusy):
            await session.run("hello", TelegramSink(bot, 1))


async def test_sdk_failure_closes_the_session_and_reports(bot, config):
    session = ChatSession(1, bot, config)

    class Exploding(FakeClient):
        async def receive_response(self):
            raise RuntimeError("cli went away")
            yield  # pragma: no cover

    session._client = Exploding([])
    await session.run("hi", TelegramSink(bot, 1))

    assert bot.find("RuntimeError")
    assert bot.find("cli went away")
    assert session._client is None


async def test_reset_clears_state_and_sets_resume(bot, config):
    session = ChatSession(1, bot, config)
    session._client = FakeClient([])
    session.state.session_id = "old"
    session.state.total_cost_usd = 1.0
    session.broker.always_allow.add("Bash")

    await session.reset(resume="sess-9")

    assert session.state.session_id is None
    assert session.state.resume_from == "sess-9"
    assert session.state.total_cost_usd == 0.0
    assert session.broker.always_allow == set()
    assert session._client is None


async def test_healthy_rate_limit_events_are_not_reported(bot, config):
    from claude_agent_sdk import RateLimitEvent, RateLimitInfo

    session = ChatSession(1, bot, config)
    info = RateLimitInfo(
        status="allowed",
        resets_at=1788811200,
        rate_limit_type="five_hour",
        utilization=None,
        overage_status="rejected",
        overage_resets_at=None,
        overage_disabled_reason=None,
        raw={},
    )
    await _run(session, bot, [RateLimitEvent(rate_limit_info=info, uuid="u1", session_id="s1")])
    assert bot.sent == []


async def test_restricting_rate_limit_events_are_reported(bot, config):
    from claude_agent_sdk import RateLimitEvent, RateLimitInfo

    session = ChatSession(1, bot, config)
    info = RateLimitInfo(
        status="rejected",
        resets_at=1788811200,
        rate_limit_type="five_hour",
        utilization=None,
        overage_status="rejected",
        overage_resets_at=None,
        overage_disabled_reason=None,
        raw={},
    )
    await _run(session, bot, [RateLimitEvent(rate_limit_info=info, uuid="u1", session_id="s1")])
    assert bot.find("rate limit rejected")
    assert bot.find("five_hour")


async def test_internal_cli_diagnostics_are_not_shown(bot, config):
    session = ChatSession(1, bot, config)
    messages = [
        ResultMessage(
            subtype="success", duration_ms=100, duration_api_ms=90, is_error=False,
            num_turns=1, session_id="s", stop_reason="end_turn", total_cost_usd=0.0,
            usage=None, result=None, structured_output=None, model_usage=None,
            permission_denials=None, deferred_tool_use=None,
            errors=["[ede_diagnostic] result_type=user", "real problem happened"],
            api_error_status=None, uuid="u", terminal_reason="completed",
        ),
    ]
    await _run(session, bot, messages)

    assert not bot.find("ede_diagnostic")
    assert bot.find("real problem happened")
    assert not bot.find("completed")  # a normal ending needs no label


async def test_interrupt_suppresses_the_rejection_boilerplate(bot, config):
    session = ChatSession(1, bot, config)
    session._client = FakeClient([])
    async with session._turn_lock:
        await session.interrupt()
    assert session._interrupted is True

    await _run(
        session,
        bot,
        [
            UserMessage(
                content=[
                    ToolResultBlock(
                        tool_use_id="t1",
                        content="The user doesn't want to proceed with this tool use.",
                        is_error=True,
                    )
                ],
                uuid="u1", parent_tool_use_id=None, tool_use_result=None, origin=None,
            )
        ],
    )
    # run() clears the flag, so this transcript renders as a normal one...
    assert bot.find("tool error")

    # ...but while the interrupt is in flight, it stays quiet.
    bot.sent.clear()
    session._interrupted = True
    await session._on_user(
        UserMessage(
            content=[ToolResultBlock(tool_use_id="t2", content="rejected", is_error=True)],
            uuid="u2", parent_tool_use_id=None, tool_use_result=None, origin=None,
        ),
        TelegramSink(bot, 1),
    )
    assert bot.sent == []


def test_each_session_is_given_its_own_id(bot, config):
    """The CLI inherits CLAUDE_CODE_SESSION_ID from its parent, so two sessions
    started from one process would otherwise collide on a single session id."""
    import uuid

    first = ChatSession(1, bot, config)._build_options().session_id
    second = ChatSession(2, bot, config)._build_options().session_id

    assert first and second and first != second
    uuid.UUID(first)  # the CLI requires a UUID
    uuid.UUID(second)


def test_resuming_does_not_force_a_new_id(bot, config):
    session = ChatSession(1, bot, config)
    session.state.resume_from = "sess-from-yesterday"

    options = session._build_options()

    assert options.session_id is None
    assert options.resume == "sess-from-yesterday"


def test_options_carry_the_bots_own_workspace_and_settings(bot, config):
    session = ChatSession(1, bot, config)
    options = session._build_options()

    assert options.cwd == str(config.workspace_root)
    assert options.permission_mode == config.permission_mode
    assert options.can_use_tool is session.broker
