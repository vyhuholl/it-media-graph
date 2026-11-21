"""A press on a YouTube rising offer's card, through the dispatcher.

Mirrors the Telegram press tests: the same OfflineSession, the same
guarantees — a deciding press writes once and redraws the card decided,
a decided card changes nothing, a press is taken in the YouTube chat
alone, and nobody who pressed is recorded. Ids, handles and titles are
fake and spelled so.
"""

import json
import logging
from datetime import UTC, datetime
from typing import Any

import pytest
from aiogram.types import (
    CallbackQuery,
    Chat,
    Message,
    MessageEntity,
    Update,
    User,
)
from sqlalchemy import text
from test_bot_handlers import (
    OPERATOR,
    YOUTUBE_CHAT,
    OfflineSession,
    calls,
    feed,
    labels,
)

from itgraph.db.session import Database

CHANNEL = "UCfakeRisingLinks0000001"
PRESSER = User(
    id=7008, is_bot=False, first_name="Fakename", username="fake_presser"
)
CARD = (
    '📈 Растёт: <a href="https://www.youtube.com/@fake_handle">'
    "@fake_handle</a>\n"
    "Fake &lt;Rising&gt;"
)


async def an_offer(
    database_url: str,
    *,
    delivered: bool = True,
    language: str | None = None,
) -> int:
    """A channel offered today, its card sent unless said otherwise,
    with a payload that names ``language`` when given."""
    database = Database(database_url)
    try:
        async with database.session() as session:
            await session.execute(
                text(
                    "INSERT INTO yt_channels (channel_id, discovered_via) "
                    "VALUES (:id, 'telegram')"
                ),
                {"id": CHANNEL},
            )
            if language is not None:
                payload = json.dumps(
                    {
                        "snippet": {
                            "title": "Fake <Rising>",
                            "customUrl": "@fake_handle",
                            "defaultLanguage": language,
                        }
                    }
                )
                await session.execute(
                    text(
                        "INSERT INTO yt_raw_channels "
                        "(channel_id, payload, fetched_at) VALUES "
                        "(:id, CAST(:payload AS jsonb), now())"
                    ),
                    {"id": CHANNEL, "payload": payload},
                )
            await session.execute(
                text(
                    "INSERT INTO yt_rising_offer_days "
                    "(day, computed_at, size_links) "
                    "VALUES ('2026-11-19', now(), 3)"
                )
            )
            offer_id = await session.scalar(
                text(
                    "INSERT INTO yt_rising_offers "
                    "(day, channel_id, families, families_before, "
                    "discovered_via, topic, offered_at, delivered_at) "
                    "VALUES ('2026-11-19', :id, 2, 0, 'telegram', "
                    "'en', now(), CASE WHEN :sent THEN now() END) "
                    "RETURNING id"
                ),
                {"id": CHANNEL, "sent": delivered},
            )
        assert isinstance(offer_id, int)
        return offer_id
    finally:
        await database.dispose()


async def decision_of(database_url: str) -> tuple[Any, ...]:
    database = Database(database_url)
    try:
        async with database.session() as session:
            row = (
                await session.execute(
                    text(
                        "SELECT decision::text, kind::text, language::text, "
                        "reject_reason::text, decided_at IS NOT NULL "
                        "FROM yt_rising_offers"
                    )
                )
            ).one()
        return tuple(row)
    finally:
        await database.dispose()


def card_press(chat_id: int, data: str) -> Update:
    """A press on a card, by someone with a name and a username."""
    return Update(
        update_id=4,
        callback_query=CallbackQuery(
            id="3",
            from_user=PRESSER,
            chat_instance="3",
            data=data,
            message=Message(
                message_id=55,
                date=datetime(2026, 11, 19, 8, 0, tzinfo=UTC),
                chat=Chat(id=chat_id, type="supergroup"),
                text="📈 Растёт: @fake_handle\nFake <Rising>",
                entities=[
                    MessageEntity(
                        type="text_link",
                        offset=11,
                        length=12,
                        url="https://www.youtube.com/@fake_handle",
                    )
                ],
            ),
        ),
    )


async def press(
    database_url: str, data: str, chat_id: int = YOUTUBE_CHAT
) -> OfflineSession:
    _, session = await feed(database_url, card_press(chat_id, data))
    return session


def answered(session: OfflineSession) -> list[str | None]:
    return [
        getattr(m, "text", None) for m in calls(session, "AnswerCallbackQuery")
    ]


async def test_seed_asks_for_a_kind_and_records_nothing(
    database_url: str,
) -> None:
    offer = await an_offer(database_url)

    session = await press(database_url, f"yv:{offer}:seed")

    (edit,) = calls(session, "EditMessageReplyMarkup")
    assert labels(edit.reply_markup)[:2] == ["👤 Личный", "🏢 Компания"]
    assert labels(edit.reply_markup)[-1] == "↩️ Назад"
    assert await decision_of(database_url) == (None, None, None, None, False)


async def test_a_kind_of_an_unknown_language_asks_for_it(
    database_url: str,
) -> None:
    offer = await an_offer(database_url, language=None)

    session = await press(database_url, f"yv:{offer}:kind:media")

    (edit,) = calls(session, "EditMessageReplyMarkup")
    assert labels(edit.reply_markup) == [
        "🇬🇧 EN",
        "🇷🇺 RU",
        "🌐 Другой",
        "↩️ Назад",
    ]
    assert await decision_of(database_url) == (None, None, None, None, False)


async def test_a_seed_of_a_known_language_takes_two_presses(
    database_url: str,
) -> None:
    """The payload says `ru`: the kind press decides, silently."""
    offer = await an_offer(database_url, language="ru")

    session = await press(database_url, f"yv:{offer}:kind:media")

    assert await decision_of(database_url) == (
        "seed",
        "media",
        "ru",
        None,
        True,
    )
    (edit,) = calls(session, "EditMessageText")
    assert edit.text.endswith("\n\n✅ В сиды · медиа · RU")
    assert edit.text.startswith(CARD)
    assert edit.reply_markup is None


async def test_a_seed_of_an_unknown_language_takes_three(
    database_url: str,
) -> None:
    offer = await an_offer(database_url)

    await press(database_url, f"yv:{offer}:kind:education")
    session = await press(database_url, f"yv:{offer}:lang:education:ru")

    assert await decision_of(database_url) == (
        "seed",
        "education",
        "ru",
        None,
        True,
    )
    (edit,) = calls(session, "EditMessageText")
    assert edit.text.endswith("✅ В сиды · обучение · RU")


async def test_reject_asks_for_a_reason_then_decides(
    database_url: str,
) -> None:
    offer = await an_offer(database_url)

    asked = await press(database_url, f"yv:{offer}:reject")
    (edit,) = calls(asked, "EditMessageReplyMarkup")
    assert "📱 Гаджеты" in labels(edit.reply_markup)

    decided = await press(database_url, f"yv:{offer}:reason:consumer_tech")
    assert await decision_of(database_url) == (
        "rejected",
        None,
        None,
        "consumer_tech",
        True,
    )
    (said,) = calls(decided, "EditMessageText")
    assert said.text.endswith("🚫 Отклонён · гаджеты")


async def test_maybe_is_one_press(database_url: str) -> None:
    offer = await an_offer(database_url)

    session = await press(database_url, f"yv:{offer}:maybe")

    assert await decision_of(database_url) == (
        "maybe",
        None,
        None,
        None,
        True,
    )
    (said,) = calls(session, "EditMessageText")
    assert said.text.endswith("🤔 Может быть")


async def test_back_shows_the_three_again(database_url: str) -> None:
    offer = await an_offer(database_url)

    session = await press(database_url, f"yv:{offer}:back")

    (edit,) = calls(session, "EditMessageReplyMarkup")
    assert labels(edit.reply_markup) == [
        "🌱 В сиды",
        "🤔 Может быть",
        "🚫 Отклонить",
    ]
    assert await decision_of(database_url) == (None, None, None, None, False)


async def test_of_two_presses_one_decision_is_recorded(
    database_url: str,
) -> None:
    offer = await an_offer(database_url)
    await press(database_url, f"yv:{offer}:lang:media:ru")

    late = await press(database_url, f"yv:{offer}:reason:crypto")

    assert await decision_of(database_url) == (
        "seed",
        "media",
        "ru",
        None,
        True,
    )
    assert answered(late) == ["уже размечено"]
    (said,) = calls(late, "EditMessageText")
    assert said.text.endswith("✅ В сиды · медиа · RU")


async def test_a_step_on_a_decided_card_is_told_so(
    database_url: str,
) -> None:
    offer = await an_offer(database_url)
    await press(database_url, f"yv:{offer}:maybe")

    late = await press(database_url, f"yv:{offer}:seed")

    assert answered(late) == ["уже размечено"]
    assert calls(late, "EditMessageReplyMarkup") == []


async def test_an_unknown_offer_is_answered_and_writes_nothing(
    database_url: str,
) -> None:
    await an_offer(database_url)

    session = await press(database_url, "yv:99999:maybe")

    assert answered(session) == ["нет такого предложения"]
    assert await decision_of(database_url) == (None, None, None, None, False)
    assert calls(session, "EditMessageText") == []


async def test_a_press_in_the_telegram_chat_decides_nothing(
    database_url: str,
) -> None:
    offer = await an_offer(database_url)

    session = await press(database_url, f"yv:{offer}:maybe", OPERATOR)

    assert await decision_of(database_url) == (None, None, None, None, False)
    assert calls(session, "EditMessageText") == []


async def test_nobody_who_pressed_is_recorded(
    database_url: str, caplog: pytest.LogCaptureFixture
) -> None:
    offer = await an_offer(database_url)

    with caplog.at_level(logging.DEBUG):
        await press(database_url, f"yv:{offer}:lang:media:en")

    database = Database(database_url)
    try:
        async with database.session() as session:
            row = await session.scalar(
                text("SELECT o::text FROM yt_rising_offers o")
            )
    finally:
        await database.dispose()
    assert isinstance(row, str)
    for trace in (str(PRESSER.id), "Fakename", "fake_presser"):
        assert trace not in row
        assert trace not in caplog.text
