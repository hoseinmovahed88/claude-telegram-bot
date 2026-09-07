from __future__ import annotations

import asyncio

from claude_agent_sdk import (
    PermissionResultAllow,
    PermissionResultDeny,
)
from conftest import permission_context as _context

from claude_telegram_bot.permissions import (
    ALLOW,
    ALWAYS,
    DENY,
    STOP,
    PermissionBroker,
    PermissionRegistry,
)


async def _answer(broker: PermissionBroker, action: str) -> None:
    """Wait for the prompt to register, then press a button."""
    for _ in range(200):
        if broker._pending:
            break
        await asyncio.sleep(0.005)
    request_id = next(iter(broker._pending))
    assert broker._registry.dispatch(request_id, action) is not None


async def _ask(broker: PermissionBroker, action: str, tool: str = "Bash"):
    task = asyncio.create_task(
        broker(tool, {"command": "rm -rf /"}, _context())
    )
    await _answer(broker, action)
    return await task


async def test_allow_returns_the_original_input(bot, config):
    broker = PermissionBroker(PermissionRegistry(), bot, 1, timeout=5, workspace_root="/w")
    result = await _ask(broker, ALLOW)

    assert isinstance(result, PermissionResultAllow)
    assert result.updated_input == {"command": "rm -rf /"}
    assert bot.find("Permission requested")
    assert bot.find("rm -rf /")


async def test_deny_lets_the_agent_continue(bot, config):
    broker = PermissionBroker(PermissionRegistry(), bot, 1, timeout=5, workspace_root="/w")
    result = await _ask(broker, DENY)

    assert isinstance(result, PermissionResultDeny)
    assert result.interrupt is False
    assert "Bash" in result.message


async def test_deny_and_stop_interrupts(bot, config):
    broker = PermissionBroker(PermissionRegistry(), bot, 1, timeout=5, workspace_root="/w")
    result = await _ask(broker, STOP)

    assert isinstance(result, PermissionResultDeny)
    assert result.interrupt is True


async def test_always_allow_skips_later_prompts(bot, config):
    broker = PermissionBroker(PermissionRegistry(), bot, 1, timeout=5, workspace_root="/w")
    await _ask(broker, ALWAYS)
    assert broker.always_allow == {"Bash"}

    prompts_before = len(bot.sent)
    result = await broker("Bash", {"command": "ls"}, _context())

    assert isinstance(result, PermissionResultAllow)
    assert len(bot.sent) == prompts_before  # no second prompt


async def test_timeout_denies_and_interrupts(bot, config):
    broker = PermissionBroker(PermissionRegistry(), bot, 1, timeout=0.05, workspace_root="/w")
    result = await broker("Write", {"file_path": "/w/x"}, _context())

    assert isinstance(result, PermissionResultDeny)
    assert result.interrupt is True
    assert "0.05" in result.message or "0s" in result.message
    assert any("timed out" in text for _, text in bot.edits)


async def test_stale_button_press_is_ignored(bot, config):
    broker = PermissionBroker(PermissionRegistry(), bot, 1, timeout=5, workspace_root="/w")
    await _ask(broker, ALLOW)
    assert broker._registry.dispatch("deadbeefcafe", ALLOW) is None


async def test_cancel_all_denies_everything_waiting(bot, config):
    broker = PermissionBroker(PermissionRegistry(), bot, 1, timeout=5, workspace_root="/w")
    task = asyncio.create_task(broker("Bash", {"command": "sleep 1"}, _context()))
    for _ in range(200):
        if broker._pending:
            break
        await asyncio.sleep(0.005)

    broker.cancel_all("interrupted")
    result = await task

    assert isinstance(result, PermissionResultDeny)
    assert broker._pending == {}


async def test_the_prompt_is_edited_to_show_the_outcome(bot, config):
    broker = PermissionBroker(PermissionRegistry(), bot, 1, timeout=5, workspace_root="/w")
    await _ask(broker, ALLOW)
    assert bot.edits
    assert "allowed" in bot.edits[-1][1]


async def test_a_reason_that_repeats_the_body_is_not_echoed(bot, config):
    broker = PermissionBroker(PermissionRegistry(), bot, 1, timeout=5, workspace_root="/w")
    context = _context()
    context.decision_reason = "note.txt"

    task = asyncio.create_task(
        broker("Write", {"file_path": "/w/note.txt", "content": "x"}, context)
    )
    await _answer(broker, ALLOW)
    await task

    prompt = bot.sent[0].text
    assert prompt.count("note.txt") == 1


async def test_a_distinct_reason_is_shown(bot, config):
    broker = PermissionBroker(PermissionRegistry(), bot, 1, timeout=5, workspace_root="/w")
    context = _context()
    context.decision_reason = "writes outside the project root"

    task = asyncio.create_task(broker("Write", {"file_path": "/w/a"}, context))
    await _answer(broker, ALLOW)
    await task

    assert "writes outside the project root" in bot.sent[0].text
