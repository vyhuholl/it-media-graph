"""Sending Telegram's rising offers: a card each, in order, with buttons.

The Bot API is a recorder. Ids, usernames and titles are fake and
spelled so.
"""

from datetime import UTC, date, datetime, timedelta

import pytest
from sqlalchemy import select, text

from itgraph.bot.app import BotStats, Sender, deliver_once
from itgraph.bot.destinations import Destination
from itgraph.bot.review import Keyboard, first_step
from itgraph.config import settings
from itgraph.db.channels import (
    DiscoveredChannel,
    mark_channel,
    upsert_channels,
)
from itgraph.db.edges import EdgeRow, insert_edges
from itgraph.db.models import (
    ChannelKind,
    ChannelStatus,
    DiscoverySource,
    EdgeKind,
    RisingOffer,
)
from itgraph.db.offers import NewOffer, store_day
from itgraph.db.raw import store_messages
from itgraph.db.session import Database

CHAT = 100000001
NOW = datetime(2026, 11, 18, 9, 0, tzinfo=UTC)  # 12:00 in Moscow
DAY = date(2026, 11, 18)
SEEDS = (1000000001, 1000000002, 1000000003, 1000000004)
OFFERED = tuple(2000000001 + n for n in range(5))


class CardRecorder(Sender):
    """Remembers each message with its preview and its buttons."""

    def __init__(self, *, fail_on: frozenset[int] = frozenset()) -> None:
        self.sent: list[tuple[str, str | None, Keyboard | None]] = []
        self.fail_on = fail_on
        self.calls = 0

    async def send(
        self,
        text: str,
        *,
        destination: Destination,
        preview: bool = True,
        preview_url: str | None = None,
        keyboard: Keyboard | None = None,
    ) -> None:
        self.calls += 1
        if self.calls in self.fail_on:
            raise RuntimeError("telegram is unreachable")
        assert destination.chat_id == CHAT
        self.sent.append((text, preview_url, keyboard))


@pytest.fixture(autouse=True)
def configured(monkeypatch: pytest.MonkeyPatch) -> None:
    """Telegram's chat alone, quiet hours as in production, the smallest
    cap there is."""
    monkeypatch.setattr(settings, "alert_chat_id", CHAT)
    monkeypatch.setattr(settings, "telegram_alert_topic", None)
    monkeypatch.setattr(settings, "youtube_alert_topic_en", None)
    monkeypatch.setattr(settings, "youtube_alert_topic_ru", None)
    monkeypatch.setattr(settings, "alert_quiet_from_hour", 2)
    monkeypatch.setattr(settings, "alert_quiet_to_hour", 7)
    monkeypatch.setattr(settings, "alert_digest_hour", 23)
    monkeypatch.setattr(settings, "alert_daily_cap", 1)
    monkeypatch.setattr(settings, "watch_timezone", "Europe/Moscow")


async def arrange(database: Database, *, offered: int = 5) -> None:
    """Four seeds, each its own family, and a day's offers of channels
    the first three referred to yesterday."""
    async with database.session() as session:
        await upsert_channels(
            session,
            [
                *(
                    DiscoveredChannel(seed, f"fake_seed_{n}", None, False)
                    for n, seed in enumerate(SEEDS)
                ),
                *(
                    DiscoveredChannel(
                        tg_id, f"fake_rising_{n}", f"Fake Rising {n}", False
                    )
                    for n, tg_id in enumerate(OFFERED)
                ),
            ],
            discovered_via=DiscoverySource.FORWARD,
        )
        for seed in SEEDS:
            await mark_channel(
                session,
                seed,
                status=ChannelStatus.SEED,
                kind=ChannelKind.MEDIA,
            )
        await insert_edges(
            session,
            [
                EdgeRow(
                    seed,
                    OFFERED[0],
                    EdgeKind.FORWARD,
                    100 + n,
                    NOW - timedelta(days=1, hours=n),
                )
                for n, seed in enumerate(SEEDS)
            ],
        )
        await store_day(
            session,
            day=DAY,
            computed_at=NOW - timedelta(hours=1),
            size=5,
            offers=[
                NewOffer(
                    rank=rank,
                    channel_id=tg_id,
                    families=3,
                    families_before=0,
                    first_in_window=NOW - timedelta(days=2),
                )
                for rank, tg_id in enumerate(OFFERED[:offered], start=1)
            ],
        )


async def deliver(
    database: Database, sender: Sender, *, now: datetime = NOW
) -> BotStats:
    stats = BotStats()
    await deliver_once(database, sender, stats, now=now)
    return stats


async def delivered(database: Database) -> dict[int, tuple[bool, int]]:
    async with database.session() as session:
        rows = await session.execute(
            select(
                RisingOffer.channel_id,
                RisingOffer.delivered_at.is_not(None),
                RisingOffer.attempts,
            )
        )
        return {channel: (done, attempts) for channel, done, attempts in rows}


async def offer_ids(database: Database) -> list[int]:
    async with database.session() as session:
        return list(
            await session.scalars(
                select(RisingOffer.id).order_by(RisingOffer.rank)
            )
        )


async def a_cap_reached(database: Database) -> None:
    """The cap of one spent by an alert sent an hour ago, and another
    alert waiting behind it."""
    async with database.session() as session:
        await store_messages(
            session,
            channel_id=SEEDS[0],
            payloads={
                msg_id: {
                    "_": "Message",
                    "id": msg_id,
                    "message": "a fake post",
                }
                for msg_id in (900, 901)
            },
        )
        await session.execute(
            text(
                "INSERT INTO tg_alerts (kind, channel_id, msg_id, band, value, "
                "raised_at, delivered_at, delivery) VALUES "
                "('views_spike', :seed, 900, 1, 4.2, :hour, :hour, 'direct'), "
                "('views_spike', :seed, 901, 1, 4.2, :now, NULL, NULL)"
            ),
            {"seed": SEEDS[0], "hour": NOW - timedelta(hours=1), "now": NOW},
        )


async def sent_directly(database: Database) -> int:
    async with database.session() as session:
        count = await session.scalar(
            text("SELECT count(*) FROM tg_alerts WHERE delivery = 'direct'")
        )
    return int(count or 0)


async def test_the_day_s_cards_in_rank_order_uncapped(
    database: Database,
) -> None:
    await arrange(database)
    await a_cap_reached(database)
    sender = CardRecorder()

    stats = await deliver(database, sender)

    # The cap held the alert, sent the five cards, and counts what it did.
    assert stats.sent == 0
    assert await sent_directly(database) == 1
    assert stats.offers == 5
    assert [url for _, url, _ in sender.sent] == [
        f"https://t.me/fake_rising_{n}" for n in range(5)
    ]
    ids = await offer_ids(database)
    assert [keyboard for _, _, keyboard in sender.sent] == [
        first_step(offer_id) for offer_id in ids
    ]
    assert all(done for done, _ in (await delivered(database)).values())


async def test_a_card_shows_the_channel_and_the_seed_posts(
    database: Database,
) -> None:
    await arrange(database)
    sender = CardRecorder()

    await deliver(database, sender)

    card = sender.sent[0][0]
    assert '<a href="https://t.me/fake_rising_0">@fake_rising_0</a>' in card
    assert "<b>Fake Rising 0</b>" in card
    assert "3 семьи за 14 дней, раньше ни одной" in card
    # Three of the four families, the newest references first.
    for n in range(3):
        assert f"https://t.me/fake_seed_{n}/{100 + n}" in card
    assert "fake_seed_3" not in card


async def test_nothing_in_quiet_hours(database: Database) -> None:
    await arrange(database)
    sender = CardRecorder()

    stats = await deliver(
        database, sender, now=datetime(2026, 11, 18, 0, 0, tzinfo=UTC)
    )

    assert stats.offers == 0
    assert sender.sent == []


async def test_a_failed_card_is_tried_again_and_the_sent_are_not(
    database: Database,
) -> None:
    await arrange(database, offered=3)
    failing = CardRecorder(fail_on=frozenset({2}))

    await deliver(database, failing)

    assert len(failing.sent) == 1
    state = await delivered(database)
    assert state[OFFERED[0]] == (True, 0)
    assert state[OFFERED[1]] == (False, 1)
    assert state[OFFERED[2]] == (False, 0)

    again = CardRecorder()
    await deliver(database, again)

    assert [url for _, url, _ in again.sent] == [
        "https://t.me/fake_rising_1",
        "https://t.me/fake_rising_2",
    ]


async def test_an_offer_reviewed_elsewhere_is_closed_unsent(
    database: Database,
) -> None:
    await arrange(database, offered=2)
    async with database.session() as session:
        await mark_channel(
            session,
            OFFERED[0],
            status=ChannelStatus.SEED,
            kind=ChannelKind.MEDIA,
        )
    sender = CardRecorder()

    await deliver(database, sender)

    assert [url for _, url, _ in sender.sent] == ["https://t.me/fake_rising_1"]
    assert (await delivered(database))[OFFERED[0]] == (True, 0)


async def test_an_offer_whose_channel_lost_its_username_is_closed_unsent(
    database: Database,
) -> None:
    """A card names and links the channel by its username: without one
    there is nothing to show."""
    await arrange(database, offered=2)
    async with database.session() as session:
        await session.execute(
            text("UPDATE tg_channels SET username = NULL WHERE tg_id = :id"),
            {"id": OFFERED[0]},
        )
    sender = CardRecorder()

    await deliver(database, sender)

    assert [url for _, url, _ in sender.sent] == ["https://t.me/fake_rising_1"]
    assert (await delivered(database))[OFFERED[0]] == (True, 0)
