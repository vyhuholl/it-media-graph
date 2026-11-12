"""The weekly lists' delivery: to its topic, in parts, once.

Against a real Postgres, as YouTube's alerts are: a list's entries go
with their readings, and what a message shows is read at sending.
"""

from datetime import UTC, date, datetime, timedelta

import pytest
from sqlalchemy import text
from test_bot import Recorder
from yt_alert_seed import fake_video, seed_alert, seed_channel

from itgraph.bot.app import BotStats, deliver_once
from itgraph.bot.destinations import Destination
from itgraph.config import settings
from itgraph.db.models import (
    YtAlertRoute,
    YtLang,
    YtMetric,
    YtRawVideo,
    YtRawVideoStat,
)
from itgraph.db.session import Database
from itgraph.db.yt_weekly import NewEntry, NewList, store_week

# Monday 23 November 2026, 10:00 UTC; the zone is UTC in these tests.
NOW = datetime(2026, 11, 23, 10, 0, tzinfo=UTC)
WEEK = date(2026, 11, 16)
CHAT = -100000000000017
TOPIC_EN = 11
TOPIC_RU = 12


@pytest.fixture(autouse=True)
def configured(monkeypatch: pytest.MonkeyPatch) -> None:
    """YouTube's chat and both topics, quiet hours off, the digest far."""
    monkeypatch.setattr(settings, "alert_chat_id", None)
    monkeypatch.setattr(settings, "youtube_alert_chat_id", CHAT)
    monkeypatch.setattr(settings, "youtube_alert_topic_en", TOPIC_EN)
    monkeypatch.setattr(settings, "youtube_alert_topic_ru", TOPIC_RU)
    monkeypatch.setattr(settings, "youtube_alert_quiet_from_hour", 0)
    monkeypatch.setattr(settings, "youtube_alert_quiet_to_hour", 0)
    monkeypatch.setattr(settings, "youtube_alert_digest_hour", 23)
    monkeypatch.setattr(settings, "youtube_alert_timezone", "UTC")
    monkeypatch.setattr(settings, "youtube_scoring_short_max_seconds", 180)


class ListRecorder(Recorder):
    """A recorder that also remembers whether a preview was asked for."""

    def __init__(
        self, *, fail: bool = False, fail_on: frozenset[int] = frozenset()
    ) -> None:
        super().__init__(fail=fail, fail_on=fail_on)
        self.previews: list[bool] = []

    async def send(
        self, text: str, *, destination: Destination, preview: bool = True
    ) -> None:
        await super().send(text, destination=destination)
        self.previews.append(preview)


class RefusesTopic(ListRecorder):
    def __init__(self, topic: int) -> None:
        super().__init__()
        self.topic = topic

    async def send(
        self, text: str, *, destination: Destination, preview: bool = True
    ) -> None:
        if destination.topic_id == self.topic:
            raise RuntimeError("Bad Request: message thread not found")
        await super().send(text, destination=destination, preview=preview)


async def deliver(
    database: Database, sender: Recorder, *, now: datetime = NOW
) -> BotStats:
    stats = BotStats()
    await deliver_once(database, sender, stats, now=now)
    return stats


async def a_video(
    database: Database,
    *,
    channel: str,
    number: int,
    duration: str = "PT20M",
    title: str = "A fake talk",
) -> None:
    """A video of the week, read at a day and at five days."""
    published = NOW - timedelta(days=6)
    async with database.session() as session:
        session.add(
            YtRawVideo(
                video_id=fake_video(number),
                payload={
                    "id": fake_video(number),
                    "snippet": {
                        "channelId": channel,
                        "title": title,
                        "publishedAt": published.isoformat(),
                    },
                    "contentDetails": {"duration": duration},
                },
                fetched_at=NOW,
                discovered_at=published,
                discovered_via="count",
            )
        )
        await session.flush()
        for days, views in ((1, 8_200), (5, 12_345)):
            session.add(
                YtRawVideoStat(
                    video_id=fake_video(number),
                    observed_at=published + timedelta(days=days),
                    views=views,
                    likes=None,
                    comments=90,
                )
            )


async def a_list(
    database: Database,
    route: YtAlertRoute,
    entries: list[tuple[str, int]],
    *,
    week: date = WEEK,
) -> None:
    """A stored list of (channel, video number), ranked as given."""
    async with database.session() as session:
        await store_week(
            session,
            [
                NewList(
                    week_start=week,
                    route=route,
                    computed_at=NOW,
                    threshold=2.0,
                    size=10,
                    comments_min=10,
                    videos_scored=40,
                    channels_above=len(entries),
                    entries=tuple(
                        NewEntry(
                            rank=rank,
                            channel_id=channel,
                            video_id=fake_video(number),
                            scored_at=NOW - timedelta(days=5),
                            metric=YtMetric.VIEWS,
                            value=4.2,
                            expected=2_000.0,
                            others=0,
                        )
                        for rank, (channel, number) in enumerate(
                            entries, start=1
                        )
                    ),
                )
            ],
        )


async def undelivered(database: Database) -> tuple[int, int]:
    """(lists, entries) not yet delivered."""
    async with database.session() as session:
        row = (
            await session.execute(
                text(
                    "SELECT (SELECT count(*) FROM yt_weekly_lists "
                    "WHERE delivered_at IS NULL), "
                    "(SELECT count(*) FROM yt_weekly_entries "
                    "WHERE delivered_at IS NULL)"
                )
            )
        ).one()
    return (row[0], row[1])


async def channels(database: Database, count: int, lang: YtLang) -> list[str]:
    async with database.session() as session:
        return [
            await seed_channel(session, n, lang=lang, fetched_at=NOW)
            for n in range(1, count + 1)
        ]


# --- to its topic, once ------------------------------------------------


async def test_a_list_goes_to_its_topic_without_a_preview(
    database: Database,
) -> None:
    (channel,) = await channels(database, 1, YtLang.RU)
    await a_video(database, channel=channel, number=1, duration="PT45S")
    await a_list(database, YtAlertRoute.RU, [(channel, 1)])
    sender = ListRecorder()

    stats = await deliver(database, sender)

    ((text_, destination),) = sender.messages
    assert destination.topic_id == TOPIC_RU
    assert sender.previews == [False]
    assert "YouTube · RU — выше нормы за неделю 16–22 ноября" in text_
    assert "1. <b>A fake talk</b> — A fake channel 1 · Shorts" in text_
    assert f"https://www.youtube.com/watch?v={fake_video(1)}" in text_
    # The newest counts, likes not reported; the multiple of the scored
    # reading over its expectation.
    assert "просмотры 12 345 · комментарии 90" in text_
    assert "в 4,1 раза выше обычного" in text_
    assert stats.lists == 1
    assert await undelivered(database) == (0, 0)

    await deliver(database, sender)
    assert len(sender.messages) == 1


async def test_an_empty_list_is_sent(database: Database) -> None:
    await a_list(database, YtAlertRoute.EN, [])
    sender = ListRecorder()

    await deliver(database, sender)

    ((text_, destination),) = sender.messages
    assert destination.topic_id == TOPIC_EN
    assert "Выше нормы — ничего." in text_
    assert "оценено 40 видео" in text_
    assert await undelivered(database) == (0, 0)


async def test_lists_go_oldest_week_first(database: Database) -> None:
    await a_list(database, YtAlertRoute.EN, [])
    await a_list(database, YtAlertRoute.EN, [], week=WEEK - timedelta(days=7))
    sender = ListRecorder()

    await deliver(database, sender)

    assert ["9–15 ноября" in t for t, _ in sender.messages] == [True, False]


async def test_a_long_list_goes_in_parts_and_a_failed_part_alone_again(
    database: Database,
) -> None:
    found = await channels(database, 10, YtLang.EN)
    for number, channel in enumerate(found, start=1):
        await a_video(
            database,
            channel=channel,
            number=number,
            title="Очень длинное название видео " * 12,
        )
    await a_list(
        database,
        YtAlertRoute.EN,
        [(channel, number) for number, channel in enumerate(found, start=1)],
    )
    sender = ListRecorder(fail_on=frozenset({2}))

    await deliver(database, sender)

    (first,) = sender.messages
    assert first[0].splitlines()[0].endswith(")")
    lists, entries = await undelivered(database)
    assert lists == 1 and 0 < entries < 10
    async with database.session() as session:
        attempts = await session.scalar(
            text("SELECT attempts FROM yt_weekly_lists")
        )
    assert attempts == 1

    await deliver(database, sender)

    assert len(sender.messages) >= 2
    resumed = sender.messages[1][0]
    assert "продолжение списка за неделю 16–22 ноября" in resumed
    # The numbering goes on from the part already sent.
    assert f"\n{10 - entries + 1}. <b>" in f"\n{resumed}"
    assert await undelivered(database) == (0, 0)
    shown = "".join(t for t, _ in sender.messages)
    for number in range(1, 11):
        assert shown.count(f"watch?v={fake_video(number)}") == 1


async def test_a_video_gone_before_sending_closes_the_ranks(
    database: Database,
) -> None:
    first, second = await channels(database, 2, YtLang.EN)
    await a_video(database, channel=first, number=1)
    await a_video(database, channel=second, number=2)
    await a_list(database, YtAlertRoute.EN, [(first, 1), (second, 2)])
    async with database.session() as session:
        await session.execute(
            text("DELETE FROM yt_raw_videos WHERE video_id = :id"),
            {"id": fake_video(1)},
        )
    sender = ListRecorder()

    await deliver(database, sender)

    ((text_, _),) = sender.messages
    assert fake_video(1) not in text_
    assert "1. <b>A fake talk</b> — A fake channel 2" in text_


# --- neither capped nor sent in quiet hours ----------------------------


async def test_the_cap_does_not_count_a_list(
    database: Database, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(settings, "youtube_alert_daily_cap", 1)
    (channel,) = await channels(database, 1, YtLang.EN)
    async with database.session() as session:
        await seed_alert(session, channel_id=channel, video=7, now=NOW)
        await seed_alert(session, channel_id=channel, video=8, now=NOW)
    await a_list(database, YtAlertRoute.EN, [])
    sender = ListRecorder()

    await deliver(database, sender)

    texts = [t for t, _ in sender.messages]
    assert sum("Выше нормы — ничего." in t for t in texts) == 1
    assert len(texts) == 2  # one alert under the cap, and the list


async def test_a_list_waits_out_quiet_hours(
    database: Database, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(settings, "youtube_alert_quiet_from_hour", 2)
    monkeypatch.setattr(settings, "youtube_alert_quiet_to_hour", 7)
    monkeypatch.setattr(settings, "youtube_alert_digest_hour", 9)
    await a_list(database, YtAlertRoute.EN, [])
    sender = ListRecorder()

    await deliver(database, sender, now=NOW.replace(hour=3))
    assert sender.messages == []

    await deliver(database, sender, now=NOW.replace(hour=7, minute=5))
    assert len(sender.messages) == 1


# --- failing and missing ----------------------------------------------


async def test_a_refused_topic_fails_and_goes_nowhere_else(
    database: Database,
) -> None:
    await a_list(database, YtAlertRoute.EN, [])
    sender = RefusesTopic(TOPIC_EN)

    await deliver(database, sender)

    assert sender.messages == []
    async with database.session() as session:
        row = (
            await session.execute(
                text("SELECT attempts, last_error FROM yt_weekly_lists")
            )
        ).one()
    assert row[0] == 1 and "thread not found" in row[1]


async def test_no_youtube_chat_sends_no_list(
    database: Database, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(settings, "youtube_alert_chat_id", None)
    monkeypatch.setattr(settings, "youtube_alert_topic_en", None)
    monkeypatch.setattr(settings, "youtube_alert_topic_ru", None)
    await a_list(database, YtAlertRoute.EN, [])
    sender = ListRecorder()

    await deliver(database, sender)

    assert sender.messages == []
    assert await undelivered(database) == (1, 0)


async def test_a_list_does_not_move_once_computed(database: Database) -> None:
    """Computed as Russian, relabelled English before it went: it stays
    the Russian list, unlike an alert, which is routed when sent."""
    (channel,) = await channels(database, 1, YtLang.RU)
    await a_video(database, channel=channel, number=1)
    await a_list(database, YtAlertRoute.RU, [(channel, 1)])
    async with database.session() as session:
        await session.execute(text("UPDATE yt_channels SET lang = 'en'"))
    sender = ListRecorder()

    await deliver(database, sender)

    ((text_, destination),) = sender.messages
    assert destination.topic_id == TOPIC_RU
    assert "YouTube · RU" in text_
    assert fake_video(1) in text_


async def test_a_list_whose_videos_all_went_says_so(
    database: Database, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Every video gone between the claim and the read of its evidence: the
    list was not empty, and the message does not say it was."""
    (channel,) = await channels(database, 1, YtLang.EN)
    await a_video(database, channel=channel, number=1)
    await a_list(database, YtAlertRoute.EN, [(channel, 1)])

    async def gone(*args: object, **kwargs: object) -> None:
        return None

    monkeypatch.setattr("itgraph.bot.app.entry_evidence", gone)
    sender = ListRecorder()

    await deliver(database, sender)

    ((text_, _),) = sender.messages
    assert "больше не доступны на YouTube" in text_
    assert "Выше нормы — ничего" not in text_
    async with database.session() as session:
        assert (
            await session.scalar(
                text(
                    "SELECT count(*) FROM yt_weekly_lists "
                    "WHERE delivered_at IS NULL"
                )
            )
            == 0
        )
