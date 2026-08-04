"""The aiogram binding.

Deliberately the thinnest module in the change, so this file is short.
What it does cover is the guarantee that cannot be checked anywhere else:
a message from a chat that is not the operator's must not reach a
handler. The bot's username is discoverable, so strangers will find it,
and what it can be asked about is the operator's own subscriptions.
"""

from datetime import UTC, datetime

from aiogram import Bot
from aiogram.dispatcher.event.bases import UNHANDLED
from aiogram.types import Chat, Message, Update

from itgraph.bot.handlers import build_dispatcher, verdict_keyboard
from itgraph.db.session import Database

OPERATOR = 4242
STRANGER = 9999


def test_the_keyboard_carries_the_alert_it_answers_for() -> None:
    """Without the id in the callback, a verdict has no subject."""
    markup = verdict_keyboard(17)

    data = [
        button.callback_data
        for row in markup.inline_keyboard
        for button in row
    ]
    assert data == ["useful:17", "boring:17"]


def test_there_is_no_mute_button() -> None:
    """Muting manages volume; volume arrives with the scoring change.

    A suppression rule designed against one alert a day would look
    correct because nothing tests it.
    """
    markup = verdict_keyboard(1)
    labels = [button.text for row in markup.inline_keyboard for button in row]

    assert len(labels) == 2
    assert not any("mute" in label.lower() for label in labels)


def message_from(chat_id: int) -> Update:
    """A `/status` from some chat, as aiogram would deliver it."""
    return Update(
        update_id=1,
        message=Message(
            message_id=1,
            date=datetime(2026, 8, 4, 12, 0, tzinfo=UTC),
            chat=Chat(id=chat_id, type="private"),
            text="/status",
        ),
    )


async def test_a_stranger_is_not_answered(database_url: str) -> None:
    """The guarantee that cannot be checked anywhere else.

    Driven through the dispatcher rather than asserted structurally,
    because what matters is the outcome: an outsider asking `/status`
    learns nothing about the inventory.
    """
    database = Database(database_url)
    try:
        dispatcher = build_dispatcher(database, OPERATOR)
        bot = Bot(token="1:test-bot-token-not-a-real-one")
        try:
            outcome = await dispatcher.feed_update(bot, message_from(STRANGER))
        finally:
            await bot.session.close()
    finally:
        await database.dispose()

    # `UNHANDLED` is aiogram's way of saying no handler matched.
    assert outcome is UNHANDLED


def test_the_two_interactions_are_registered(database_url: str) -> None:
    database = Database(database_url)
    dispatcher = build_dispatcher(database, OPERATOR)

    assert len(dispatcher.message.handlers) == 1
    assert len(dispatcher.callback_query.handlers) == 1
