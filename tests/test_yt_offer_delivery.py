"""Sending YouTube's rising offers: a card each, to its topic, with
buttons.

The Bot API is a recorder. Ids, handles and titles are fake and spelled
so.
"""

import json
from datetime import UTC, date, datetime

import pytest
from sqlalchemy import text
from yt_alert_seed import fake_video, seed_alert, seed_channel

from itgraph.bot.app import BotStats, Sender, deliver_once
from itgraph.bot.destinations import Destination
from itgraph.bot.review import Keyboard
from itgraph.bot.yt_review import first_step
from itgraph.config import settings
from itgraph.db.models import (
    ChannelStatus,
    YtChannel,
    YtChannelKind,
    YtDiscoverySource,
    YtLang,
    YtRawChannel,
    YtRisingOffer,
    YtRisingOfferDay,
    YtRisingRanking,
)
from itgraph.db.session import Database
from itgraph.db.yt_channels import Review, mark_channel
from itgraph.db.yt_chart import store_chart

CHAT = -100000000000017
TOPIC_EN = 11
TOPIC_RU = 12
NOW = datetime(2026, 11, 19, 8, 0, tzinfo=UTC)  # 11:00 in Moscow
DAY = date(2026, 11, 19)

CHARTED_US = "UCfakeRisingCharts000001"
CHARTED_RU = "UCfakeRisingCharts000002"
LINKED_RU = "UCfakeRisingLinks0000001"
LINKED_NEW = "UCfakeRisingLinks0000002"
VIDEO = "fakeVid0001"


class CardRecorder(Sender):
    """Remembers each message with its preview and its buttons; topics
    in ``refuse`` raise, as a deleted topic does."""

    def __init__(self, *, refuse: frozenset[int] = frozenset()) -> None:
        self.sent: list[
            tuple[str, Destination, str | None, Keyboard | None]
        ] = []
        self.refuse = refuse
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
        if destination.topic_id in self.refuse:
            raise RuntimeError("the topic is gone")
        assert destination.chat_id == CHAT
        self.sent.append((text, destination, preview_url, keyboard))


@pytest.fixture(autouse=True)
def configured(monkeypatch: pytest.MonkeyPatch) -> None:
    """The chat and both YouTube topics, quiet hours off, in UTC."""
    monkeypatch.setattr(settings, "alert_chat_id", CHAT)
    monkeypatch.setattr(settings, "telegram_alert_topic", None)
    monkeypatch.setattr(settings, "youtube_alert_topic_en", TOPIC_EN)
    monkeypatch.setattr(settings, "youtube_alert_topic_ru", TOPIC_RU)
    monkeypatch.setattr(settings, "youtube_alert_quiet_from_hour", 0)
    monkeypatch.setattr(settings, "youtube_alert_quiet_to_hour", 0)
    monkeypatch.setattr(settings, "youtube_alert_digest_hour", 23)
    monkeypatch.setattr(settings, "youtube_alert_timezone", "UTC")


async def arrange(
    database: Database,
    *channels: str,
    region: str | None = None,
    video: str | None = None,
) -> None:
    """A day with no offers yet, and the day's chart readings: US holds
    ``video`` for the first channel given, RU the second."""
    async with database.session() as session:
        session.add(
            YtRisingOfferDay(
                day=DAY, computed_at=NOW, size_chart=3, size_links=3
            )
        )
        await store_chart(
            session,
            "US",
            {
                "items": [
                    {
                        "id": video or VIDEO,
                        "snippet": {
                            "channelId": channels[0],
                            "title": "Fake Chart Top",
                        },
                    }
                ]
            },
            fetched_at=NOW,
        )
        await store_chart(
            session,
            "RU",
            {
                "items": [
                    {
                        "id": "fakeVid0002",
                        "snippet": {"channelId": CHARTED_RU},
                    }
                ]
            },
            fetched_at=NOW,
        )
        await session.commit()


async def an_offer(
    database: Database,
    channel: str,
    *,
    ranking: YtRisingRanking,
    topic: str,
    payload: dict | None = None,
    region: str | None = None,
    position: int | None = None,
    families: int | None = None,
    families_before: int | None = None,
) -> None:
    """One offered channel, a candidate record its own."""
    async with database.session() as session:
        session.add(
            YtChannel(
                channel_id=channel,
                discovered_via=(
                    YtDiscoverySource.CHART
                    if ranking is YtRisingRanking.CHART
                    else YtDiscoverySource.TELEGRAM
                ),
            )
        )
        if payload is not None:
            session.add(
                YtRawChannel(
                    channel_id=channel, payload=payload, fetched_at=NOW
                )
            )
        session.add(
            YtRisingOffer(
                day=DAY,
                channel_id=channel,
                ranking=ranking,
                region=region,
                position=position,
                families=families,
                families_before=families_before,
                first_in_window=NOW,
                discovered_via=(
                    YtDiscoverySource.CHART
                    if ranking is YtRisingRanking.CHART
                    else YtDiscoverySource.TELEGRAM
                ),
                topic=topic,
                offered_at=NOW,
            )
        )
        await session.commit()


def payload_of(
    title: str | None,
    *,
    handle: str | None = None,
    language: str | None = None,
) -> dict:
    snippet: dict = {"title": title}
    if handle:
        snippet["customUrl"] = f"@{handle}"
    if language:
        snippet["defaultLanguage"] = language
    return json.loads(json.dumps({"snippet": snippet}))


async def deliver(
    database: Database, sender: Sender, *, now: datetime = NOW
) -> BotStats:
    stats = BotStats()
    await deliver_once(database, sender, stats, now=now)
    return stats


async def state_of(database: Database, channel: str) -> tuple[bool, int]:
    """The channel's offer: delivered, attempts."""
    async with database.session() as session:
        row = (
            await session.execute(
                text(
                    "SELECT delivered_at IS NOT NULL, attempts "
                    "FROM yt_rising_offers WHERE channel_id = :id"
                ),
                {"id": channel},
            )
        ).one()
    return (row[0], row[1])


async def offer_ids(database: Database) -> list[int]:
    async with database.session() as session:
        return list(
            await session.scalars(
                text("SELECT id FROM yt_rising_offers ORDER BY channel_id")
            )
        )


def topic_of(sender: CardRecorder) -> list[int | None]:
    return [destination.topic_id for _, destination, _, _ in sender.sent]


async def test_chart_offers_go_to_their_regions_topics(
    database: Database,
) -> None:
    await arrange(database, CHARTED_US, CHARTED_RU)
    await an_offer(
        database,
        CHARTED_US,
        ranking=YtRisingRanking.CHART,
        topic="en",
        region="US",
        position=1,
    )
    await an_offer(
        database,
        CHARTED_RU,
        ranking=YtRisingRanking.CHART,
        topic="ru",
        region="RU",
        position=2,
    )
    sender = CardRecorder()

    stats = await deliver(database, sender)

    assert topic_of(sender) == [TOPIC_EN, TOPIC_RU]
    assert stats.offers == 2
    us_card = sender.sent[0][0]
    assert "📈 Чарт US, позиция 1" in us_card
    assert f"https://www.youtube.com/channel/{CHARTED_US}" in us_card
    assert (
        f'<a href="https://www.youtube.com/watch?v={VIDEO}">'
        "Fake Chart Top</a>" in us_card
    )
    ru_card = sender.sent[1][0]
    assert "📈 Чарт RU, позиция 2" in ru_card
    # RU's reading holds no title: the video is named by its id.
    assert (
        '<a href="https://www.youtube.com/watch?v=fakeVid0002">'
        "fakeVid0002</a>" in ru_card
    )
    # No language to ask about on a chart offer's card.
    assert "Язык" not in us_card
    for _, _, _, keyboard in sender.sent:
        assert keyboard is not None
        assert [label for row in keyboard for label, _ in row] == [
            "🌱 В сиды",
            "🤔 Может быть",
            "🚫 Отклонить",
        ]


async def test_a_link_offer_of_a_known_language_reads_it_at_sending(
    database: Database,
) -> None:
    """Offered English by default; the payload fetched since says
    Russian, and the card follows it to the Russian topic."""
    await arrange(database, LINKED_RU)
    await an_offer(
        database,
        LINKED_RU,
        ranking=YtRisingRanking.LINK,
        topic="en",
        payload=payload_of(
            "Fake <Rising> & Co", handle="fake_handle", language="ru"
        ),
        families=3,
        families_before=1,
    )
    sender = CardRecorder()

    await deliver(database, sender)

    assert topic_of(sender) == [TOPIC_RU]
    card, _, preview_url, _ = sender.sent[0]
    assert "Fake &lt;Rising&gt; &amp; Co" in card
    assert "3 семьи за 14 дней, 1 раньше" in card
    # A language a topic takes is not marked: the topic says it.
    assert "Язык" not in card
    assert preview_url == "https://www.youtube.com/@fake_handle"


async def test_a_payload_less_link_offer_reads_in_english(
    database: Database,
) -> None:
    """A day-one card: no payload, no title, the language unknown."""
    await arrange(database, LINKED_NEW)
    await an_offer(
        database,
        LINKED_NEW,
        ranking=YtRisingRanking.LINK,
        topic="en",
        families=2,
        families_before=0,
    )
    sender = CardRecorder()

    await deliver(database, sender)

    assert topic_of(sender) == [TOPIC_EN]
    card, _, preview_url, _ = sender.sent[0]
    assert "раньше ни одной" in card
    assert "Язык: не определён" in card
    assert LINKED_NEW in card
    assert preview_url == f"https://www.youtube.com/channel/{LINKED_NEW}"


async def test_nothing_in_quiet_hours(
    database: Database, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(settings, "youtube_alert_quiet_from_hour", 2)
    monkeypatch.setattr(settings, "youtube_alert_quiet_to_hour", 7)
    await arrange(database, LINKED_NEW)
    await an_offer(
        database,
        LINKED_NEW,
        ranking=YtRisingRanking.LINK,
        topic="en",
        families=2,
    )
    sender = CardRecorder()

    stats = await deliver(
        database, sender, now=datetime(2026, 11, 19, 3, 0, tzinfo=UTC)
    )

    assert stats.offers == 0
    assert sender.sent == []


async def test_the_cards_spend_nothing_of_the_topic_s_cap(
    database: Database, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A topic that has spent its daily cap of alerts still gets its
    cards: an offer is no alert."""
    monkeypatch.setattr(settings, "youtube_alert_daily_cap", 1)
    await arrange(database, CHARTED_US)
    async with database.session() as session:
        channel = await seed_channel(
            session, 1, lang=YtLang.EN, fetched_at=NOW
        )
        for video in (1, 2):
            await seed_alert(session, channel_id=channel, video=video, now=NOW)
    await an_offer(
        database,
        CHARTED_US,
        ranking=YtRisingRanking.CHART,
        topic="en",
        region="US",
        position=1,
    )
    sender = CardRecorder()

    stats = await deliver(database, sender)

    assert stats.sent == 1  # the cap's one alert
    assert stats.offers == 1  # and the card, past the cap
    async with database.session() as session:
        held = await session.scalar(
            text("SELECT count(*) FROM yt_alerts WHERE held_at IS NOT NULL")
        )
    assert held == 1


async def test_a_refused_topic_fails_and_reaches_nowhere_else(
    database: Database,
) -> None:
    """The English topic is gone: its offers fail, nothing of them goes
    to the Russian topic or to General, and the Russian topic's own
    cards still go."""
    await arrange(database, CHARTED_US, CHARTED_RU)
    await an_offer(
        database,
        CHARTED_US,
        ranking=YtRisingRanking.CHART,
        topic="en",
        region="US",
        position=1,
    )
    await an_offer(
        database,
        LINKED_NEW,
        ranking=YtRisingRanking.LINK,
        topic="en",
        families=2,
    )
    await an_offer(
        database,
        CHARTED_RU,
        ranking=YtRisingRanking.CHART,
        topic="ru",
        region="RU",
        position=1,
    )
    sender = CardRecorder(refuse=frozenset({TOPIC_EN}))

    stats = await deliver(database, sender)

    assert stats.offers == 1
    assert topic_of(sender) == [TOPIC_RU]
    assert await state_of(database, CHARTED_US) == (False, 1)
    assert await state_of(database, LINKED_NEW) == (False, 0)
    assert await state_of(database, CHARTED_RU) == (True, 0)


async def test_a_failed_card_is_tried_again_and_the_sent_are_not(
    database: Database,
) -> None:
    await arrange(database, CHARTED_RU)
    await an_offer(
        database,
        CHARTED_RU,
        ranking=YtRisingRanking.CHART,
        topic="ru",
        region="RU",
        position=1,
    )
    refusing = CardRecorder(refuse=frozenset({TOPIC_RU}))

    await deliver(database, refusing)

    assert await state_of(database, CHARTED_RU) == (False, 1)

    again = CardRecorder()
    await deliver(database, again)

    assert topic_of(again) == [TOPIC_RU]
    assert await state_of(database, CHARTED_RU) == (True, 1)


async def test_an_offer_reviewed_elsewhere_is_closed_unsent(
    database: Database,
) -> None:
    await arrange(database, LINKED_NEW)
    await an_offer(
        database,
        LINKED_NEW,
        ranking=YtRisingRanking.LINK,
        topic="en",
        families=2,
    )
    async with database.session() as session:
        await mark_channel(
            session,
            LINKED_NEW,
            Review(
                status=ChannelStatus.SEED,
                kind=YtChannelKind.MEDIA,
                lang=YtLang.EN,
            ),
        )
    sender = CardRecorder()

    await deliver(database, sender)

    assert sender.sent == []
    assert await state_of(database, LINKED_NEW) == (True, 0)


async def test_the_cards_carry_the_offer_s_own_buttons(
    database: Database,
) -> None:
    await arrange(database, LINKED_NEW)
    await an_offer(
        database,
        LINKED_NEW,
        ranking=YtRisingRanking.LINK,
        topic="en",
        families=2,
    )
    sender = CardRecorder()

    await deliver(database, sender)

    (offer_id,) = await offer_ids(database)
    _, _, _, keyboard = sender.sent[0]
    assert keyboard == first_step(offer_id)


async def test_a_chart_offer_without_a_reading_loses_its_video_line(
    database: Database,
) -> None:
    """A region no reading of the day holds: the card still names the
    place, and no video is linked."""
    await arrange(database, CHARTED_US)
    await an_offer(
        database,
        CHARTED_US,
        ranking=YtRisingRanking.CHART,
        topic="ru",
        region="GB",
        position=7,
    )
    sender = CardRecorder()

    await deliver(database, sender)

    card = sender.sent[0][0]
    assert "📈 Чарт GB, позиция 7" in card
    assert "🎬" not in card
    assert fake_video(1) not in card
