from __future__ import annotations

import asyncio

import pytest
from telegram.error import InvalidToken, TelegramError

from claude_telegram_bot import runner
from claude_telegram_bot.runner import StartupError, run_bots


class FakeUpdater:
    def __init__(self, app: FakeApp) -> None:
        self._app = app
        self.running = False

    async def start_polling(self, **kwargs):
        self.running = True
        self._app.log.append("poll")

    async def stop(self):
        self.running = False
        self._app.log.append("unpoll")


class FakeManager:
    def __init__(self, app: FakeApp) -> None:
        self._app = app

    async def shutdown(self):
        self._app.log.append("sessions-closed")


class FakeApp:
    def __init__(self, name: str, trace: list[str], fail: Exception | None = None) -> None:
        self.name = name
        self.trace = trace
        self.log: list[str] = []
        self.fail = fail
        self.running = False
        self.updater = FakeUpdater(self)
        self.bot_data = {"sessions": FakeManager(self)}
        self.bot = self

    async def get_me(self):
        return type("Me", (), {"username": f"{self.name}_bot"})()

    async def initialize(self):
        if self.fail:
            raise self.fail
        self.log.append("init")

    async def start(self):
        self.running = True
        self.log.append("start")
        self.trace.append(f"start:{self.name}")

    async def stop(self):
        self.running = False
        self.log.append("stop")
        self.trace.append(f"stop:{self.name}")

    async def shutdown(self):
        self.log.append("shutdown")


def _wire(monkeypatch, apps: dict):
    monkeypatch.setattr(runner, "build_application", lambda config: apps[config.name])


def _configs(config, *names):
    return [type(config)(**{**config.__dict__, "name": n, "bot_token": f"{i}:t"})
            for i, n in enumerate(names)]


async def test_every_bot_starts_and_stops(config, monkeypatch):
    trace: list[str] = []
    apps = {n: FakeApp(n, trace) for n in ("work", "blog")}
    _wire(monkeypatch, apps)

    stop = asyncio.Event()
    task = asyncio.create_task(run_bots(_configs(config, "work", "blog"), stop=stop))
    await asyncio.sleep(0.05)

    assert trace == ["start:work", "start:blog"]

    stop.set()
    await asyncio.wait_for(task, timeout=5)

    # Unwound in reverse, so the last bot up is the first down.
    assert trace[2:] == ["stop:blog", "stop:work"]
    for app in apps.values():
        assert app.log == ["init", "start", "poll", "sessions-closed", "unpoll", "stop", "shutdown"]


async def test_claude_sessions_are_closed_before_telegram_is_torn_down(config, monkeypatch):
    """Application.shutdown() never runs post_shutdown, so the runner must close
    the sessions itself or the CLI subprocesses are orphaned."""
    trace: list[str] = []
    apps = {"work": FakeApp("work", trace)}
    _wire(monkeypatch, apps)

    stop = asyncio.Event()
    task = asyncio.create_task(run_bots(_configs(config, "work"), stop=stop))
    await asyncio.sleep(0.05)
    stop.set()
    await asyncio.wait_for(task, timeout=5)

    log = apps["work"].log
    assert "sessions-closed" in log
    assert log.index("sessions-closed") < log.index("shutdown")


async def test_a_rejected_token_names_the_bot_and_unwinds_the_rest(config, monkeypatch):
    trace: list[str] = []
    apps = {
        "work": FakeApp("work", trace),
        "blog": FakeApp("blog", trace, fail=InvalidToken("bad token")),
    }
    _wire(monkeypatch, apps)

    with pytest.raises(StartupError, match="blog"):
        await run_bots(_configs(config, "work", "blog"), stop=asyncio.Event())

    # The bot that did come up was shut down cleanly.
    assert trace == ["start:work", "stop:work"]
    assert "sessions-closed" in apps["work"].log


async def test_a_telegram_error_on_startup_is_reported_not_swallowed(config, monkeypatch):
    apps = {"work": FakeApp("work", [], fail=TelegramError("network down"))}
    _wire(monkeypatch, apps)

    with pytest.raises(StartupError, match="network down"):
        await run_bots(_configs(config, "work"), stop=asyncio.Event())


async def test_a_failure_while_stopping_one_bot_does_not_strand_the_others(
    config, monkeypatch
):
    trace: list[str] = []

    class Stubborn(FakeApp):
        async def stop(self):
            raise RuntimeError("stop blew up")

    apps = {"work": FakeApp("work", trace), "blog": Stubborn("blog", trace)}
    _wire(monkeypatch, apps)

    stop = asyncio.Event()
    task = asyncio.create_task(run_bots(_configs(config, "work", "blog"), stop=stop))
    await asyncio.sleep(0.05)
    stop.set()
    await asyncio.wait_for(task, timeout=5)

    assert "stop:work" in trace
