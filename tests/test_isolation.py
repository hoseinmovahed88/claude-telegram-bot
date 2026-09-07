"""Two bots in one process must share nothing: no sessions, no grants, no
pending permission requests."""

from __future__ import annotations

import asyncio

from claude_agent_sdk import PermissionResultAllow, PermissionResultDeny
from conftest import permission_context as _context

from claude_telegram_bot.handlers import build_application
from claude_telegram_bot.session import SessionManager


def _two_bots(config, tmp_path):
    work = type(config)(
        **{**config.__dict__, "name": "work", "bot_token": "111:a",
           "workspace_root": tmp_path / "work"}
    )
    blog = type(config)(
        **{**config.__dict__, "name": "blog", "bot_token": "222:b",
           "workspace_root": tmp_path / "blog"}
    )
    (tmp_path / "work").mkdir(exist_ok=True)
    (tmp_path / "blog").mkdir(exist_ok=True)
    return work, blog


def test_each_application_gets_its_own_manager_and_registry(config, tmp_path):
    work, blog = _two_bots(config, tmp_path)
    app_a, app_b = build_application(work), build_application(blog)

    manager_a: SessionManager = app_a.bot_data["sessions"]
    manager_b: SessionManager = app_b.bot_data["sessions"]

    assert manager_a is not manager_b
    assert manager_a.registry is not manager_b.registry
    assert app_a.bot_data["config"].workspace_root != app_b.bot_data["config"].workspace_root


def test_the_same_chat_id_gets_a_separate_session_per_bot(config, bot, tmp_path):
    work, blog = _two_bots(config, tmp_path)
    manager_a = SessionManager(work)
    manager_b = SessionManager(blog)

    session_a = manager_a.get(1, bot)
    session_b = manager_b.get(1, bot)

    assert session_a is not session_b
    assert session_a.state.cwd == work.workspace_root
    assert session_b.state.cwd == blog.workspace_root


def test_an_always_allow_grant_does_not_leak_to_the_other_bot(config, bot, tmp_path):
    work, blog = _two_bots(config, tmp_path)
    manager_a, manager_b = SessionManager(work), SessionManager(blog)

    manager_a.get(1, bot).broker.always_allow.add("Bash")

    assert manager_b.get(1, bot).broker.always_allow == set()


async def test_one_bots_button_cannot_resolve_the_other_bots_request(
    config, bot, tmp_path
):
    work, blog = _two_bots(config, tmp_path)
    manager_a, manager_b = SessionManager(work), SessionManager(blog)
    broker_a = manager_a.get(1, bot).broker

    task = asyncio.create_task(broker_a("Bash", {"command": "rm -rf /"}, _context()))
    for _ in range(200):
        if broker_a._pending:
            break
        await asyncio.sleep(0.005)
    request_id = next(iter(broker_a._pending))

    # The other bot has never heard of this request.
    assert manager_b.registry.dispatch(request_id, "a") is None
    assert not task.done()

    # Its own registry still resolves it.
    assert manager_a.registry.dispatch(request_id, "a") is not None
    assert isinstance(await task, PermissionResultAllow)


async def test_stopping_one_bot_does_not_cancel_the_others_pending_request(
    config, bot, tmp_path
):
    work, blog = _two_bots(config, tmp_path)
    manager_a, manager_b = SessionManager(work), SessionManager(blog)
    broker_a = manager_a.get(1, bot).broker
    broker_b = manager_b.get(1, bot).broker

    task_a = asyncio.create_task(broker_a("Bash", {"command": "a"}, _context()))
    task_b = asyncio.create_task(broker_b("Bash", {"command": "b"}, _context()))
    for _ in range(200):
        if broker_a._pending and broker_b._pending:
            break
        await asyncio.sleep(0.005)

    broker_b.cancel_all("bot blog stopped")

    assert isinstance(await task_b, PermissionResultDeny)
    assert not task_a.done()

    request_id = next(iter(broker_a._pending))
    manager_a.registry.dispatch(request_id, "a")
    assert isinstance(await task_a, PermissionResultAllow)
