"""The /cd confinement is the one guard between a chat message and the filesystem."""

from __future__ import annotations

from types import SimpleNamespace

import pytest

from claude_telegram_bot.handlers import build_application, change_dir


class Recorder:
    def __init__(self) -> None:
        self.replies: list[str] = []

    async def reply_html(self, text: str, **kwargs) -> None:
        self.replies.append(text)

    async def reply_text(self, text: str, **kwargs) -> None:
        self.replies.append(text)


@pytest.fixture
def wired(config, bot):
    application = build_application(config)
    application.bot_data["config"] = config
    message = Recorder()
    update = SimpleNamespace(
        effective_message=message,
        effective_chat=SimpleNamespace(id=1),
        effective_user=SimpleNamespace(id=42, username="u"),
    )
    context = SimpleNamespace(args=[], bot=bot, application=application)
    return update, context, message, application


async def test_cd_into_a_subdirectory_is_allowed(wired, config):
    update, context, message, application = wired
    (config.workspace_root / "project").mkdir()
    context.args = ["project"]

    await change_dir(update, context)

    session = application.bot_data["sessions"].peek(1)
    assert session.state.cwd == config.workspace_root / "project"
    assert "project" in message.replies[-1]


async def test_cd_cannot_escape_the_workspace(wired, config):
    update, context, message, application = wired
    context.args = ["../../../etc"]

    await change_dir(update, context)

    session = application.bot_data["sessions"].peek(1)
    assert session.state.cwd == config.workspace_root
    assert "outside the workspace" in message.replies[-1]


async def test_cd_rejects_an_absolute_path_outside_the_workspace(wired, config):
    update, context, message, application = wired
    context.args = ["/etc"]

    await change_dir(update, context)

    assert application.bot_data["sessions"].peek(1).state.cwd == config.workspace_root
    assert "outside the workspace" in message.replies[-1]


async def test_cd_rejects_a_file(wired, config):
    update, context, message, application = wired
    (config.workspace_root / "notes.txt").write_text("x")
    context.args = ["notes.txt"]

    await change_dir(update, context)

    assert "not a directory" in message.replies[-1]


async def test_cd_with_no_argument_reports_the_current_directory(wired, config):
    update, context, message, application = wired

    await change_dir(update, context)

    assert str(config.workspace_root) in message.replies[-1]
