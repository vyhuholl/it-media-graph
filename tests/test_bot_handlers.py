"""The aiogram binding.

Deliberately the thinnest module in the change, so this file is short.
What it does cover is what only this layer decides: which chats are
answered at all, and which spellings of a command count.

The command-spelling case is here because getting it wrong fails in the
worst available direction — correctly in a private chat, silently in a
group, so the bug ships and surfaces the day the alerts are shared.
"""

from collections.abc import AsyncGenerator
from datetime import UTC, datetime, timedelta
from typing import Any

import pytest
from aiogram import Bot
from aiogram.client.session.base import BaseSession
from aiogram.dispatcher.event.bases import UNHANDLED
from aiogram.methods import TelegramMethod
from aiogram.types import Chat, Message, MessageEntity, Update, User

from itgraph.bot.handlers import build_dispatcher, verdict_keyboard
from itgraph.db.session import Database

OPERATOR = 4242
STRANGER = 9999
BOT_NAME = "itgraph_alerts_bot"


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


def command_from(chat_id: int, *, text: str, kind: str = "private") -> Update:
    """A command from some chat, as aiogram would deliver it."""
    return Update(
        update_id=1,
        message=Message(
            message_id=1,
            date=datetime(2026, 8, 4, 12, 0, tzinfo=UTC),
            chat=Chat(id=chat_id, type=kind),
            text=text,
            entities=[
                MessageEntity(
                    type="bot_command", offset=0, length=len(text.split()[0])
                )
            ],
        ),
    )


class OfflineSession(BaseSession):
    """Records what the bot tried to send instead of sending it.

    No network in tests is a project rule, and here it also buys the
    stronger assertion: the handler is proved to have produced a reply,
    not merely to have been selected by a filter.
    """

    def __init__(self) -> None:
        super().__init__()
        self.sent: list[str] = []

    async def close(self) -> None:
        return None

    async def make_request(
        self,
        bot: Bot,
        method: TelegramMethod[Any],
        timeout: int | None = None,
    ) -> Any:
        self.sent.append(getattr(method, "text", ""))
        return Message(
            message_id=2,
            date=datetime(2026, 8, 4, 12, 0, tzinfo=UTC),
            chat=Chat(id=OPERATOR, type="private"),
        )

    async def stream_content(
        self,
        url: str,
        headers: dict[str, Any] | None = None,
        timeout: int = 30,
        chunk_size: int = 65536,
        raise_for_status: bool = True,
    ) -> AsyncGenerator[bytes]:
        yield b""  # pragma: no cover - never streamed here


def offline_bot() -> Bot:
    """A bot that neither reaches the network nor asks who it is.

    ``Command`` validates a ``/status@name`` mention against the bot's
    username, which it would otherwise fetch over the network. Seeding
    the cache keeps this offline while still exercising the mention path
    — which is the point, because that is the form a group sends.
    """
    bot = Bot(
        token="1:test-bot-token-not-a-real-one", session=OfflineSession()
    )
    bot._me = User(id=1, is_bot=True, first_name="itgraph", username=BOT_NAME)
    return bot


async def feed(
    database_url: str, chat_id: int, *, text: str, kind: str = "private"
) -> tuple[object, list[str]]:
    """Drive one update through a real dispatcher. Returns outcome and replies."""
    database = Database(database_url)
    try:
        dispatcher = build_dispatcher(database, OPERATOR)
        bot = offline_bot()
        session: OfflineSession = bot.session  # type: ignore[assignment]
        try:
            outcome = await dispatcher.feed_update(
                bot, command_from(chat_id, text=text, kind=kind)
            )
        finally:
            await bot.session.close()
        return outcome, session.sent
    finally:
        await database.dispose()


async def test_a_stranger_is_not_answered(database_url: str) -> None:
    """The guarantee that cannot be checked anywhere else.

    Driven through the dispatcher rather than asserted structurally,
    because what matters is the outcome: an outsider asking `/status`
    learns nothing about the inventory.
    """
    outcome, replies = await feed(database_url, STRANGER, text="/status")

    # `UNHANDLED` is aiogram's way of saying no handler matched.
    assert outcome is UNHANDLED
    assert replies == []


async def test_status_answers_in_a_private_chat(database_url: str) -> None:
    _, replies = await feed(database_url, OPERATOR, text="/status")

    assert len(replies) == 1
    assert "оповещений всего" in replies[0]


@pytest.mark.parametrize(
    "text", [f"/status@{BOT_NAME}", "/status"], ids=["mentioned", "plain"]
)
async def test_status_answers_in_a_group_however_it_is_addressed(
    database_url: str, text: str
) -> None:
    """The bug this replaced: an exact match on `"/status"`.

    Telegram requires a command in a group to name the bot, and with
    privacy mode on — the default — that is the only form the bot is
    handed at all. An equality test therefore worked in a private chat
    and rejected precisely what a group sends, which is the way round
    that ships.
    """
    _, replies = await feed(
        database_url, OPERATOR, text=text, kind="supergroup"
    )

    assert len(replies) == 1


async def test_a_command_for_another_bot_is_not_answered(
    database_url: str,
) -> None:
    """Two bots in one group must not both reply."""
    outcome, replies = await feed(
        database_url,
        OPERATOR,
        text="/status@some_other_bot",
        kind="supergroup",
    )

    assert outcome is UNHANDLED
    assert replies == []


async def test_the_two_interactions_are_registered(database_url: str) -> None:
    database = Database(database_url)
    try:
        dispatcher = build_dispatcher(database, OPERATOR)
    finally:
        await database.dispose()

    assert len(dispatcher.message.handlers) == 1
    assert len(dispatcher.callback_query.handlers) == 1


# --- what /status can tell apart ------------------------------------


async def raise_one(database_url: str, *, ago: timedelta) -> None:
    """One alert nothing has tried to send, raised some time ago."""
    from sqlalchemy import text

    database = Database(database_url)
    try:
        async with database.session() as session:
            await session.execute(
                text(
                    "INSERT INTO channels (tg_id, username, title, "
                    "discovered_via, status) VALUES "
                    "(1, 'example', 'Example', 'manual', 'seed') "
                    "ON CONFLICT DO NOTHING"
                )
            )
            await session.execute(
                text(
                    "INSERT INTO raw_messages (channel_id, msg_id, payload) "
                    'VALUES (1, 1, \'{"_": "Message"}\'::jsonb) '
                    "ON CONFLICT DO NOTHING"
                )
            )
            await session.execute(
                text(
                    "INSERT INTO alerts (kind, channel_id, msg_id, band, "
                    "value, raised_at) VALUES "
                    "('repost_cascade', 1, 1, 2, 2, :at)"
                ),
                {"at": datetime.now(UTC) - ago},
            )
    finally:
        await database.dispose()


async def test_status_says_nothing_has_tried_to_send(
    database_url: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The state that cost an evening: alerts waiting, attempts zero.

    Undelivered-and-failing and undelivered-and-untouched mean opposite
    things — a send that keeps failing against a delivery loop that is
    not running — and only the second is invisible in every other
    signal. The service is `active`, this command answers, and the queue
    does not move.
    """
    from itgraph.config import settings

    monkeypatch.setattr(settings, "alert_quiet_from_hour", 0)
    monkeypatch.setattr(settings, "alert_quiet_to_hour", 0)
    await raise_one(database_url, ago=timedelta(hours=7))

    _, replies = await feed(database_url, OPERATOR, text="/status")

    assert "попыток не было у 1" in replies[0]
    assert "доставка не работает" in replies[0]


async def test_status_does_not_cry_broken_during_quiet_hours(
    database_url: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Held is not stuck, and the difference has to be stated.

    Quiet hours leave alerts with no attempts by design. Reporting that
    as a fault would teach the operator to ignore the one line that
    means something.
    """
    from itgraph.config import settings

    monkeypatch.setattr(settings, "alert_quiet_from_hour", 0)
    monkeypatch.setattr(settings, "alert_quiet_to_hour", 23)
    await raise_one(database_url, ago=timedelta(hours=7))

    _, replies = await feed(database_url, OPERATOR, text="/status")

    assert "тихие часы" in replies[0]
    assert "доставка не работает" not in replies[0]


async def test_status_is_quiet_when_there_is_nothing_waiting(
    database_url: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A healthy bot says its counts and nothing alarming."""
    from itgraph.config import settings

    monkeypatch.setattr(settings, "alert_quiet_from_hour", 0)
    monkeypatch.setattr(settings, "alert_quiet_to_hour", 0)

    _, replies = await feed(database_url, OPERATOR, text="/status")

    assert "⚠️" not in replies[0]
    assert "ждут отправки" not in replies[0]
