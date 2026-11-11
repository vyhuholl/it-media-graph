"""The aiogram binding.

Deliberately the thinnest module in the change, so this file is short.
What it does cover is what only this layer decides: which chats are
answered at all, which spellings of a command count, which topic a reply
lands in, and which chats the bot refuses to start with.

The command-spelling case is here because getting it wrong fails in the
worst available direction — correctly in a private chat, silently in a
group, so the bug ships and surfaces the day the alerts are shared.
"""

from collections.abc import AsyncGenerator
from datetime import UTC, datetime, timedelta
from typing import Any
from zoneinfo import ZoneInfo

import pytest
from aiogram import Bot
from aiogram.client.session.base import BaseSession
from aiogram.client.telegram import PRODUCTION
from aiogram.dispatcher.event.bases import UNHANDLED
from aiogram.exceptions import TelegramBadRequest
from aiogram.methods import GetChat, TelegramMethod
from aiogram.types import (
    CallbackQuery,
    Chat,
    ChatFullInfo,
    Message,
    MessageEntity,
    Update,
    User,
)
from pydantic import HttpUrl

from itgraph.bot import handlers
from itgraph.bot.destinations import Destination, Platform
from itgraph.bot.handlers import (
    BotSender,
    ChatUnreachableError,
    build_bot,
    build_dispatcher,
    check_chats,
)
from itgraph.db.models import YtAlertRoute
from itgraph.db.session import Database

OPERATOR = 4242
YOUTUBE_CHAT = -100777
STRANGER = 9999
TOPIC_EN = 11
TOPIC_RU = 12
BOT_NAME = "itgraph_alerts_bot"


TOKEN = "42:test-bot-token"


def destination(
    key: str,
    platform: Platform,
    chat_id: int,
    topic_id: int | None = None,
    route: YtAlertRoute | None = None,
) -> Destination:
    return Destination(
        key=key,
        platform=platform,
        chat_id=chat_id,
        topic_id=topic_id,
        setting="ALERT_CHAT_ID"
        if platform is Platform.TELEGRAM
        else "YOUTUBE_ALERT_CHAT_ID",
        daily_cap=20,
        quiet_from=0,
        quiet_to=0,
        digest_hour=9,
        zone="Europe/Moscow",
        route=route,
    )


TELEGRAM = destination("telegram", Platform.TELEGRAM, OPERATOR)
YOUTUBE_EN = destination(
    "youtube_en", Platform.YOUTUBE, YOUTUBE_CHAT, TOPIC_EN, YtAlertRoute.EN
)
YOUTUBE_RU = destination(
    "youtube_ru", Platform.YOUTUBE, YOUTUBE_CHAT, TOPIC_RU, YtAlertRoute.RU
)
ALL = [TELEGRAM, YOUTUBE_EN, YOUTUBE_RU]


def test_the_bot_api_is_telegrams_own_by_default(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(handlers.settings, "telegram_bot_api_url", None)

    assert build_bot(TOKEN).session.api == PRODUCTION


def test_a_given_address_reaches_the_session(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        handlers.settings,
        "telegram_bot_api_url",
        HttpUrl("http://stub.invalid:8081/"),
    )

    api = build_bot(TOKEN).session.api

    assert api.base == "http://stub.invalid:8081/bot{token}/{method}"


def command_from(
    chat_id: int,
    *,
    text: str,
    kind: str = "private",
    topic: int | None = None,
) -> Update:
    """A command from some chat, as aiogram would deliver it."""
    return Update(
        update_id=1,
        message=Message(
            message_id=1,
            date=datetime(2026, 8, 4, 12, 0, tzinfo=UTC),
            chat=Chat(id=chat_id, type=kind, is_forum=topic is not None),
            text=text,
            message_thread_id=topic,
            is_topic_message=topic is not None or None,
            entities=[
                MessageEntity(
                    type="bot_command", offset=0, length=len(text.split()[0])
                )
            ],
        ),
    )


def press_from(chat_id: int, *, data: str) -> Update:
    """A press on a button under one of the bot's messages."""
    return Update(
        update_id=2,
        callback_query=CallbackQuery(
            id="1",
            from_user=User(id=7, is_bot=False, first_name="someone"),
            chat_instance="1",
            data=data,
            message=Message(
                message_id=33,
                date=datetime(2026, 8, 4, 12, 0, tzinfo=UTC),
                chat=Chat(id=chat_id, type="supergroup"),
                text="an old alert",
            ),
        ),
    )


class OfflineSession(BaseSession):
    """Records what the bot asked of the Bot API instead of asking it.

    No network in tests is a project rule, and here it also buys the
    stronger assertion: the handler is proved to have produced a reply,
    not merely to have been selected by a filter. ``chats`` is what
    `getChat` answers, by id; any other id is refused as Telegram refuses
    a chat the bot is not in.
    """

    def __init__(self, chats: dict[int, bool] | None = None) -> None:
        super().__init__()
        self.calls: list[TelegramMethod[Any]] = []
        self.chats = chats or {}

    @property
    def sent(self) -> list[str]:
        return [
            getattr(method, "text", "")
            for method in self.calls
            if type(method).__name__ == "SendMessage"
        ]

    async def close(self) -> None:
        return None

    async def make_request(
        self,
        bot: Bot,
        method: TelegramMethod[Any],
        timeout: int | None = None,
    ) -> Any:
        self.calls.append(method)
        if isinstance(method, GetChat):
            chat_id = int(method.chat_id)
            if chat_id not in self.chats:
                raise TelegramBadRequest(method, "Bad Request: chat not found")
            return ChatFullInfo(
                id=chat_id,
                type="supergroup",
                is_forum=self.chats[chat_id],
                accent_color_id=0,
                max_reaction_count=11,
                accepted_gift_types={
                    "unlimited_gifts": False,
                    "limited_gifts": False,
                    "unique_gifts": False,
                    "premium_subscription": False,
                    "gifts_from_channels": False,
                },
            )
        if type(method).__name__ in {
            "AnswerCallbackQuery",
            "EditMessageReplyMarkup",
        }:
            return True
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


def offline_bot(chats: dict[int, bool] | None = None) -> Bot:
    """A bot that neither reaches the network nor asks who it is.

    ``Command`` validates a ``/status@name`` mention against the bot's
    username, which it would otherwise fetch over the network. Seeding
    the cache keeps this offline while still exercising the mention path
    — which is the point, because that is the form a group sends.
    """
    bot = Bot(
        token="1:test-bot-token-not-a-real-one", session=OfflineSession(chats)
    )
    bot._me = User(id=1, is_bot=True, first_name="itgraph", username=BOT_NAME)
    return bot


async def feed(
    database_url: str, update: Update
) -> tuple[object, OfflineSession]:
    """Drive one update through a real dispatcher. Returns the outcome and
    what the bot asked of the Bot API."""
    database = Database(database_url)
    try:
        dispatcher = build_dispatcher(database, ALL)
        bot = offline_bot()
        session: OfflineSession = bot.session  # type: ignore[assignment]
        try:
            outcome = await dispatcher.feed_update(bot, update)
        finally:
            await bot.session.close()
        return outcome, session
    finally:
        await database.dispose()


async def status(
    database_url: str,
    chat_id: int = OPERATOR,
    *,
    text: str = "/status",
    kind: str = "private",
    topic: int | None = None,
) -> tuple[object, list[str]]:
    outcome, session = await feed(
        database_url, command_from(chat_id, text=text, kind=kind, topic=topic)
    )
    return outcome, session.sent


async def test_a_stranger_is_not_answered(database_url: str) -> None:
    """The guarantee that cannot be checked anywhere else.

    Driven through the dispatcher rather than asserted structurally,
    because what matters is the outcome: an outsider asking `/status`
    learns nothing about the inventory.
    """
    outcome, replies = await status(database_url, STRANGER)

    # `UNHANDLED` is aiogram's way of saying no handler matched.
    assert outcome is UNHANDLED
    assert replies == []


async def test_status_answers_in_a_private_chat(database_url: str) -> None:
    _, replies = await status(database_url)

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
    _, replies = await status(database_url, text=text, kind="supergroup")

    assert len(replies) == 1


async def test_a_command_for_another_bot_is_not_answered(
    database_url: str,
) -> None:
    """Two bots in one group must not both reply."""
    outcome, replies = await status(
        database_url, text="/status@some_other_bot", kind="supergroup"
    )

    assert outcome is UNHANDLED
    assert replies == []


async def test_the_two_interactions_are_registered(database_url: str) -> None:
    database = Database(database_url)
    try:
        dispatcher = build_dispatcher(database, ALL)
    finally:
        await database.dispose()

    assert len(dispatcher.message.handlers) == 1
    assert len(dispatcher.callback_query.handlers) == 1


# --- the YouTube chat -----------------------------------------------


async def test_the_youtube_chat_is_told_about_youtube(
    database_url: str,
) -> None:
    _, replies = await status(
        database_url, YOUTUBE_CHAT, kind="supergroup", topic=TOPIC_RU
    )

    (reply,) = replies
    assert "оповещений YouTube всего: 0" in reply
    assert "RU: не доставлено 0" in reply
    assert "каналов в очереди опроса" not in reply


async def test_youtube_says_nothing_is_scored_before_acceptance(
    database_url: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    from itgraph import yt_policy

    monkeypatch.setattr(yt_policy, "DERIVED_METRICS_ACCEPTED_ON", None)

    _, replies = await status(database_url, YOUTUBE_CHAT, kind="supergroup")

    assert "оценок YouTube нет до одобрения" in replies[0]


async def test_the_answer_lands_in_the_topic_it_was_asked_in(
    database_url: str,
) -> None:
    _, session = await feed(
        database_url,
        command_from(
            YOUTUBE_CHAT, text="/status", kind="supergroup", topic=TOPIC_RU
        ),
    )

    (sent,) = [m for m in session.calls if type(m).__name__ == "SendMessage"]
    assert getattr(sent, "message_thread_id", None) == TOPIC_RU


async def test_general_is_answered_too(database_url: str) -> None:
    """Any topic of a configured chat, and General has no topic id."""
    outcome, replies = await status(
        database_url, YOUTUBE_CHAT, kind="supergroup"
    )

    assert outcome is not UNHANDLED
    assert len(replies) == 1


# --- old buttons ----------------------------------------------------


async def test_an_old_button_is_answered_and_taken_off(
    database_url: str,
) -> None:
    outcome, session = await feed(
        database_url, press_from(OPERATOR, data="useful:17")
    )

    names = [type(method).__name__ for method in session.calls]
    assert outcome is not UNHANDLED
    assert "AnswerCallbackQuery" in names
    (edit,) = [
        m
        for m in session.calls
        if type(m).__name__ == "EditMessageReplyMarkup"
    ]
    assert getattr(edit, "reply_markup", "unset") is None
    assert getattr(edit, "message_id", None) == 33


async def test_an_old_button_records_nothing(database_url: str) -> None:
    from sqlalchemy import text

    await feed(database_url, press_from(OPERATOR, data="boring:17"))

    database = Database(database_url)
    try:
        async with database.session() as session:
            tables = await session.scalar(
                text(
                    "SELECT count(*) FROM information_schema.tables "
                    "WHERE table_name = 'alert_feedback'"
                )
            )
    finally:
        await database.dispose()
    assert tables == 0


async def test_a_press_from_a_stranger_is_ignored(database_url: str) -> None:
    outcome, session = await feed(
        database_url, press_from(STRANGER, data="useful:17")
    )

    assert outcome is UNHANDLED
    assert session.calls == []


async def test_alerts_are_sent_without_buttons_to_their_topic() -> None:
    bot = offline_bot()
    session: OfflineSession = bot.session  # type: ignore[assignment]

    await BotSender(bot).send("an alert", destination=YOUTUBE_EN)

    (sent,) = session.calls
    assert getattr(sent, "chat_id", None) == YOUTUBE_CHAT
    assert getattr(sent, "message_thread_id", None) == TOPIC_EN
    assert getattr(sent, "reply_markup", None) is None


# --- the chats at start ---------------------------------------------


async def test_reachable_chats_pass() -> None:
    bot = offline_bot({OPERATOR: False, YOUTUBE_CHAT: True})

    await check_chats(bot, ALL)


async def test_a_chat_with_topics_that_is_no_forum_is_refused() -> None:
    bot = offline_bot({OPERATOR: False, YOUTUBE_CHAT: False})

    with pytest.raises(ChatUnreachableError) as refused:
        await check_chats(bot, ALL)

    assert refused.value.setting == "YOUTUBE_ALERT_CHAT_ID"


async def test_a_chat_the_bot_is_not_in_is_refused() -> None:
    bot = offline_bot({YOUTUBE_CHAT: True})

    with pytest.raises(ChatUnreachableError) as refused:
        await check_chats(bot, ALL)

    assert refused.value.setting == "ALERT_CHAT_ID"


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


def quiet(hour_from: int, hour_to: int) -> list[Destination]:
    from dataclasses import replace

    return [replace(d, quiet_from=hour_from, quiet_to=hour_to) for d in ALL]


async def status_under(
    database_url: str, destinations: list[Destination]
) -> list[str]:
    database = Database(database_url)
    try:
        dispatcher = build_dispatcher(database, destinations)
        bot = offline_bot()
        session: OfflineSession = bot.session  # type: ignore[assignment]
        await dispatcher.feed_update(
            bot, command_from(OPERATOR, text="/status")
        )
        return session.sent
    finally:
        await database.dispose()


async def test_status_says_nothing_has_tried_to_send(
    database_url: str,
) -> None:
    """The state that cost an evening: alerts waiting, attempts zero.

    Undelivered-and-failing and undelivered-and-untouched mean opposite
    things — a send that keeps failing against a delivery loop that is
    not running — and only the second is invisible in every other
    signal. The service is `active`, this command answers, and the queue
    does not move.
    """
    await raise_one(database_url, ago=timedelta(hours=7))

    replies = await status_under(database_url, quiet(0, 0))

    assert "попыток не было у 1" in replies[0]
    assert "доставка не работает" in replies[0]


async def test_status_does_not_cry_broken_during_quiet_hours(
    database_url: str,
) -> None:
    """Held is not stuck, and the difference has to be stated.

    Quiet hours leave alerts with no attempts by design. Reporting that
    as a fault would teach the operator to ignore the one line that
    means something.
    """
    # A window of 23 hours from the hour it is now, so the test does not
    # depend on when it runs: 0-23 left 23:00-23:59 outside, and failed then.
    hour = datetime.now(ZoneInfo("Europe/Moscow")).hour
    await raise_one(database_url, ago=timedelta(hours=7))

    replies = await status_under(database_url, quiet(hour, (hour + 23) % 24))

    assert "тихие часы" in replies[0]
    assert "доставка не работает" not in replies[0]


async def test_held_alerts_do_not_read_as_broken(database_url: str) -> None:
    """Held for the digest is waiting by design, and is said to be."""
    from sqlalchemy import text

    await raise_one(database_url, ago=timedelta(hours=7))
    database = Database(database_url)
    try:
        async with database.session() as session:
            await session.execute(text("UPDATE alerts SET held_at = now()"))
    finally:
        await database.dispose()

    replies = await status_under(database_url, quiet(0, 0))

    assert "до дайджеста: 1" in replies[0]
    assert "доставка не работает" not in replies[0]


async def test_status_is_quiet_when_there_is_nothing_waiting(
    database_url: str,
) -> None:
    """A healthy bot says its counts and nothing alarming."""
    replies = await status_under(database_url, quiet(0, 0))

    assert "⚠️" not in replies[0]
    assert "ждут отправки" not in replies[0]


async def test_status_counts_what_was_folded(database_url: str) -> None:
    """ "Raised" overstates the messages there were unless folds are named,
    and a fold waiting for its head is not one more thing undelivered."""
    from sqlalchemy import text

    database = Database(database_url)
    try:
        async with database.session() as session:
            await session.execute(
                text(
                    "INSERT INTO channels (tg_id, username, title, "
                    "discovered_via, status) VALUES "
                    "(1, 'example', 'Example', 'manual', 'seed')"
                )
            )
            await session.execute(
                text(
                    "INSERT INTO raw_messages (channel_id, msg_id, payload) "
                    'VALUES (1, 1, \'{"_": "Message"}\'::jsonb), '
                    '(1, 2, \'{"_": "Message"}\'::jsonb)'
                )
            )
            head = await session.scalar(
                text(
                    "INSERT INTO alerts (kind, channel_id, msg_id, band, "
                    "value) VALUES ('views_spike', 1, 1, 1, 6) RETURNING id"
                )
            )
            await session.execute(
                text(
                    "INSERT INTO alerts (kind, channel_id, msg_id, band, "
                    "value, folded_into) "
                    "VALUES ('views_spike', 1, 2, 1, 5, :h)"
                ),
                {"h": head},
            )
    finally:
        await database.dispose()

    replies = await status_under(database_url, quiet(0, 0))

    assert "свёрнуто в серии: 1" in replies[0]
    assert "не доставлено: 1" in replies[0]
