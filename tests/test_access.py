"""Who may reach the bot, and from where. This is the whole security boundary."""

from __future__ import annotations

import datetime as dt
from types import SimpleNamespace

from telegram import Chat, Message, Update, User
from telegram.ext import filters

from claude_telegram_bot.handlers import (
    authorised_filter,
    build_application,
    unauthorised,
)

NOW = dt.datetime.now(dt.timezone.utc)
OWNER = User(id=42, first_name="Owner", is_bot=False)
STRANGER = User(id=99, first_name="Nosy", is_bot=False)
PRIVATE = Chat(id=42, type=Chat.PRIVATE)
GROUP = Chat(id=-100123, type=Chat.SUPERGROUP)


def _message(chat: Chat, user: User, text: str = "hello") -> Message:
    return Message(message_id=1, date=NOW, chat=chat, from_user=user, text=text)


def _prompt_reaches_claude(config, update: Update) -> bool:
    """True when the update would be handled as a prompt."""
    text_filter = filters.TEXT & ~filters.COMMAND & authorised_filter(config)
    return bool(text_filter.check_update(update))


def test_the_owner_in_a_private_chat_is_allowed(config):
    assert _prompt_reaches_claude(config, Update(1, message=_message(PRIVATE, OWNER)))


def test_a_stranger_is_refused(config):
    assert not _prompt_reaches_claude(config, Update(1, message=_message(PRIVATE, STRANGER)))


def test_a_stranger_is_refused_even_in_the_owners_own_chat(config):
    """The chat id is the owner's, but the sender is not."""
    assert not _prompt_reaches_claude(
        config, Update(1, message=_message(Chat(id=42, type=Chat.PRIVATE), STRANGER))
    )


def test_the_owner_in_a_group_is_refused_by_default(config):
    """Replies carry file contents and command output to everyone in the chat."""
    assert not _prompt_reaches_claude(config, Update(1, message=_message(GROUP, OWNER)))


def test_a_group_can_be_opted_into_explicitly(config):
    opted_in = type(config)(**{**config.__dict__, "allowed_chat_ids": frozenset({GROUP.id})})
    assert _prompt_reaches_claude(opted_in, Update(1, message=_message(GROUP, OWNER)))


def test_opting_a_group_in_does_not_open_it_to_everyone(config):
    opted_in = type(config)(**{**config.__dict__, "allowed_chat_ids": frozenset({GROUP.id})})
    assert not _prompt_reaches_claude(opted_in, Update(1, message=_message(GROUP, STRANGER)))


def test_naming_chats_closes_every_other_chat(config):
    """An explicit chat list replaces the private-only default rather than adding to it."""
    elsewhere = Chat(id=-999, type=Chat.SUPERGROUP)
    opted_in = type(config)(**{**config.__dict__, "allowed_chat_ids": frozenset({GROUP.id})})
    assert not _prompt_reaches_claude(opted_in, Update(1, message=_message(elsewhere, OWNER)))


def test_editing_an_old_message_does_not_re_run_it(config):
    assert not _prompt_reaches_claude(
        config, Update(1, edited_message=_message(PRIVATE, OWNER))
    )


def test_a_channel_post_has_no_sender_and_is_refused(config):
    post = Message(message_id=9, date=NOW, chat=Chat(id=-1009, type=Chat.CHANNEL), text="hi")
    assert not _prompt_reaches_claude(config, Update(1, channel_post=post))


def test_commands_are_gated_by_the_same_rule(config):
    application = build_application(config)
    command_handlers = [
        h for handlers in application.handlers.values() for h in handlers
        if type(h).__name__ == "CommandHandler"
    ]
    assert command_handlers
    stranger = Update(1, message=_message(PRIVATE, STRANGER, "/status"))
    owner_in_group = Update(2, message=_message(GROUP, OWNER, "/status"))
    for handler in command_handlers:
        assert not handler.check_update(stranger)
        assert not handler.check_update(owner_in_group)


# ---------------------------------------------------------- stranger replies


class Recorder:
    def __init__(self) -> None:
        self.replies: list[str] = []

    async def reply_text(self, text: str, **kwargs) -> None:
        self.replies.append(text)


def _rejection(config):
    message = Recorder()
    update = SimpleNamespace(
        effective_message=message,
        effective_chat=SimpleNamespace(id=-1, type="supergroup"),
        effective_user=SimpleNamespace(id=99, username="nosy"),
    )
    context = SimpleNamespace(
        application=SimpleNamespace(bot_data={"config": config, "sessions": None})
    )
    return update, context, message


async def test_strangers_get_silence_by_default(config, caplog):
    import logging

    update, context, message = _rejection(config)
    with caplog.at_level(logging.WARNING):
        await unauthorised(update, context)

    assert message.replies == []
    # The owner still learns who tried, from the log.
    assert "99" in caplog.text and "nosy" in caplog.text


async def test_strangers_can_be_answered_if_the_owner_opts_in(config):
    chatty = type(config)(**{**config.__dict__, "reply_to_strangers": True})
    update, context, message = _rejection(chatty)
    context.application.bot_data["config"] = chatty

    await unauthorised(update, context)

    assert message.replies and "99" in message.replies[0]


def test_an_empty_allow_list_would_fail_closed(config):
    """The loader already rejects an empty allow-list. If one ever slipped
    through, the filter must match nobody rather than everybody -- PTB's
    allow_empty defaults to False, and nothing here overrides it."""
    empty = type(config)(**{**config.__dict__, "allowed_user_ids": frozenset()})
    assert not _prompt_reaches_claude(empty, Update(1, message=_message(PRIVATE, OWNER)))
    assert not _prompt_reaches_claude(empty, Update(2, message=_message(PRIVATE, STRANGER)))
