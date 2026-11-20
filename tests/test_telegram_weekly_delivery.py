"""Telegram's weekly list's delivery: to its chat, in parts, once.

Against a real Postgres: a list's entries go with their readings, and
what a message shows of a post is read at sending.
"""

import json
from datetime import UTC, date, datetime, timedelta

import pytest
from sqlalchemy import text
from test_bot import Recorder

from itgraph.bot.app import BotStats, deliver_once
from itgraph.bot.destinations import Destination, Platform
from itgraph.config import settings
from itgraph.db.models import Metric, YtAlertRoute
from itgraph.db.session import Database
from itgraph.db.weekly import NewEntry, NewList, store_week
from itgraph.db.yt_weekly import NewList as YtNewList
from itgraph.db.yt_weekly import store_week as store_yt_week

# Monday 23 November 2026, 10:00 UTC; the zone is UTC in these tests.
NOW = datetime(2026, 11, 23, 10, 0, tzinfo=UTC)
WEEK = date(2026, 11, 16)
PUBLISHED = NOW - timedelta(days=4)
SCORED_AT = PUBLISHED + timedelta(hours=2)
CHAT = 100000001
TOPIC_EN = 11
TOPIC_RU = 12
FIRST = 7000000001


@pytest.fixture(autouse=True)
def configured(monkeypatch: pytest.MonkeyPatch) -> None:
    """Telegram's chat alone, quiet hours off, the digest far."""
    monkeypatch.setattr(settings, "alert_chat_id", CHAT)
    monkeypatch.setattr(settings, "telegram_alert_topic", None)
    monkeypatch.setattr(settings, "youtube_alert_topic_en", None)
    monkeypatch.setattr(settings, "youtube_alert_topic_ru", None)
    monkeypatch.setattr(settings, "alert_quiet_from_hour", 0)
    monkeypatch.setattr(settings, "alert_quiet_to_hour", 0)
    monkeypatch.setattr(settings, "alert_digest_hour", 23)
    monkeypatch.setattr(settings, "watch_timezone", "UTC")


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


async def deliver(
    database: Database, sender: Recorder, *, now: datetime = NOW
) -> BotStats:
    stats = BotStats()
    await deliver_once(database, sender, stats, now=now)
    return stats


async def a_post(
    database: Database,
    channel: int,
    *,
    username: str | None = "fake_weekly_one",
    title: str = "A fake channel",
    msg_id: int = 10,
) -> None:
    """A channel, its post of the week, and the reading it was scored at."""
    async with database.session() as session:
        await session.execute(
            text(
                "INSERT INTO tg_channels (tg_id, username, title, "
                "discovered_via, status, kind) VALUES "
                "(:id, :name, :title, 'manual', 'seed', 'personal') "
                "ON CONFLICT DO NOTHING"
            ),
            {"id": channel, "name": username, "title": title},
        )
        await session.execute(
            text(
                "INSERT INTO tg_raw_messages (channel_id, msg_id, payload) "
                "VALUES (:c, :m, CAST(:p AS jsonb))"
            ),
            {
                "c": channel,
                "m": msg_id,
                "p": json.dumps(
                    {
                        "_": "Message",
                        "id": msg_id,
                        "date": PUBLISHED.isoformat(),
                        "message": f"Fake post {channel}\nsecond line",
                    }
                ),
            },
        )
        await session.execute(
            text(
                "INSERT INTO tg_message_metrics (channel_id, msg_id, "
                "observed_at, views) VALUES (:c, :m, :at, 900)"
            ),
            {"c": channel, "m": msg_id, "at": SCORED_AT},
        )


async def a_list(
    database: Database,
    channels: list[int],
    *,
    week: date = WEEK,
    metric: Metric = Metric.VIEWS,
    observed: float = 8_200.0,
) -> None:
    """A stored list of these channels' posts, ranked as given."""
    async with database.session() as session:
        await store_week(
            session,
            NewList(
                week_start=week,
                computed_at=NOW,
                threshold=3.7,
                size=10,
                comments_min=10,
                posts_scored=3912,
                channels_above=len(channels),
                entries=tuple(
                    NewEntry(
                        rank=rank,
                        channel_id=channel,
                        msg_id=10,
                        scored_at=SCORED_AT,
                        metric=metric,
                        value=4.24,
                        observed=observed,
                        expected=2_000.0,
                        others=2,
                    )
                    for rank, channel in enumerate(channels, start=1)
                ),
            ),
        )


async def undelivered(database: Database) -> tuple[int, int]:
    """(lists, entries) not yet delivered."""
    async with database.session() as session:
        row = (
            await session.execute(
                text(
                    "SELECT (SELECT count(*) FROM tg_weekly_lists "
                    "WHERE delivered_at IS NULL), "
                    "(SELECT count(*) FROM tg_weekly_entries "
                    "WHERE delivered_at IS NULL)"
                )
            )
        ).one()
    return (row[0], row[1])


async def attempts(database: Database) -> tuple[int, str | None]:
    async with database.session() as session:
        row = (
            await session.execute(
                text("SELECT attempts, last_error FROM tg_weekly_lists")
            )
        ).one()
    return (row[0], row[1])


def with_youtube(monkeypatch: pytest.MonkeyPatch) -> None:
    """The chat's YouTube topics beside its Telegram one."""
    monkeypatch.setattr(settings, "youtube_alert_topic_en", TOPIC_EN)
    monkeypatch.setattr(settings, "youtube_alert_topic_ru", TOPIC_RU)
    monkeypatch.setattr(settings, "youtube_alert_quiet_from_hour", 0)
    monkeypatch.setattr(settings, "youtube_alert_quiet_to_hour", 0)
    monkeypatch.setattr(settings, "youtube_alert_digest_hour", 23)
    monkeypatch.setattr(settings, "youtube_alert_timezone", "UTC")


# --- to its chat, once -------------------------------------------------


async def test_a_list_goes_to_the_chat_without_a_preview(
    database: Database,
) -> None:
    await a_post(database, FIRST)
    await a_list(database, [FIRST])
    sender = ListRecorder()

    stats = await deliver(database, sender)

    ((text_, destination),) = sender.messages
    assert destination.platform is Platform.TELEGRAM
    assert destination.chat_id == CHAT
    assert sender.previews == [False]
    assert "📋 Telegram — выше нормы за неделю 16–22 ноября" in text_
    assert "оценено 3 912 постов" in text_
    assert (
        "1. <b>A fake channel</b> (@fake_weekly_one) · опубликовано 4 дн назад"
        in text_
    )
    assert "https://t.me/fake_weekly_one/10" in text_
    assert f"Fake post {FIRST}" in text_
    assert "second line" not in text_
    assert "👁 Просмотры — в 4,1 раза выше обычного для канала (z 4.2)" in (
        text_
    )
    assert "ещё 2 поста канала выше порога" in text_
    assert stats.lists == 1
    assert await undelivered(database) == (0, 0)

    await deliver(database, sender)
    assert len(sender.messages) == 1


async def test_a_comment_entry_states_its_count(database: Database) -> None:
    await a_post(database, FIRST)
    await a_list(database, [FIRST], metric=Metric.COMMENTS, observed=37.0)
    sender = ListRecorder()

    await deliver(database, sender)

    ((text_, _),) = sender.messages
    assert "💬 Комментарии — 37, обычно около 2000 (z 4.2)" in text_


async def test_an_empty_list_is_sent(database: Database) -> None:
    await a_list(database, [])
    sender = ListRecorder()

    await deliver(database, sender)

    ((text_, _),) = sender.messages
    assert "Выше порога — ничего." in text_
    assert "оценено 3 912 постов" in text_
    assert await undelivered(database) == (0, 0)


async def test_lists_go_oldest_week_first(database: Database) -> None:
    await a_list(database, [])
    await a_list(database, [], week=WEEK - timedelta(days=7))
    sender = ListRecorder()

    await deliver(database, sender)

    assert ["9–15 ноября" in t for t, _ in sender.messages] == [True, False]


async def test_a_long_list_goes_in_parts_and_a_failed_part_alone_again(
    database: Database,
) -> None:
    channels = [FIRST + n for n in range(10)]
    for channel in channels:
        await a_post(
            database,
            channel,
            username=f"fake_weekly_{channel}",
            title="Очень длинное название канала " * 10,
        )
    await a_list(database, channels)
    sender = ListRecorder(fail_on=frozenset({2}))

    await deliver(database, sender)

    (first,) = sender.messages
    assert first[0].splitlines()[0].endswith(")")
    lists, entries = await undelivered(database)
    assert lists == 1 and 0 < entries < 10
    assert (await attempts(database))[0] == 1

    await deliver(database, sender)

    assert len(sender.messages) >= 2
    resumed = sender.messages[1][0]
    assert "продолжение списка за неделю 16–22 ноября" in resumed
    assert f"\n{10 - entries + 1}. <b>" in f"\n{resumed}"
    assert await undelivered(database) == (0, 0)
    shown = "".join(t for t, _ in sender.messages)
    for channel in channels:
        assert shown.count(f"t.me/fake_weekly_{channel}/10") == 1


async def test_a_post_gone_before_sending_closes_the_ranks(
    database: Database,
) -> None:
    second = FIRST + 1
    await a_post(database, FIRST, username="fake_weekly_one")
    await a_post(database, second, username="fake_weekly_two")
    await a_list(database, [FIRST, second])
    async with database.session() as session:
        await session.execute(
            text("DELETE FROM tg_channels WHERE tg_id = :c"), {"c": FIRST}
        )
    sender = ListRecorder()

    await deliver(database, sender)

    ((text_, _),) = sender.messages
    assert "fake_weekly_one" not in text_
    assert "1. <b>A fake channel</b> (@fake_weekly_two)" in text_
    assert await undelivered(database) == (0, 0)


async def test_a_list_whose_posts_all_went_says_so(
    database: Database, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Every post gone between the claim and the read of its evidence: the
    list was not empty, and the message does not say it was."""
    await a_post(database, FIRST)
    await a_list(database, [FIRST])

    async def gone(*args: object, **kwargs: object) -> None:
        return None

    monkeypatch.setattr("itgraph.bot.app.alert_evidence", gone)
    sender = ListRecorder()

    await deliver(database, sender)

    ((text_, _),) = sender.messages
    assert "удалены вместе с их каналами" in text_
    assert "Выше порога — ничего" not in text_
    async with database.session() as session:
        assert (
            await session.scalar(
                text(
                    "SELECT count(*) FROM tg_weekly_lists "
                    "WHERE delivered_at IS NULL"
                )
            )
            == 0
        )


# --- neither capped nor sent in quiet hours ----------------------------


async def test_the_cap_does_not_count_a_list(
    database: Database, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(settings, "alert_daily_cap", 1)
    await a_post(database, FIRST)
    async with database.session() as session:
        for msg_id in (11, 12):
            await session.execute(
                text(
                    "INSERT INTO tg_raw_messages (channel_id, msg_id, payload) "
                    "VALUES (:c, :m, CAST(:p AS jsonb))"
                ),
                {
                    "c": FIRST,
                    "m": msg_id,
                    "p": json.dumps(
                        {
                            "_": "Message",
                            "id": msg_id,
                            "date": (NOW - timedelta(hours=1)).isoformat(),
                            "message": "a fake alerted post",
                        }
                    ),
                },
            )
            await session.execute(
                text(
                    "INSERT INTO tg_alerts (kind, channel_id, msg_id, band, "
                    "value, raised_at) VALUES "
                    "('views_spike', :c, :m, 1, 5.0, :at)"
                ),
                {"c": FIRST, "m": msg_id, "at": NOW},
            )
    await a_list(database, [])
    sender = ListRecorder()

    await deliver(database, sender)

    texts = [t for t, _ in sender.messages]
    assert sum("Выше порога — ничего." in t for t in texts) == 1
    assert len(texts) == 2  # one alert under the cap, and the list
    async with database.session() as session:
        held = await session.scalar(
            text("SELECT count(*) FROM tg_alerts WHERE delivered_at IS NULL")
        )
    assert held == 1


async def test_a_list_waits_out_quiet_hours(
    database: Database, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(settings, "alert_quiet_from_hour", 2)
    monkeypatch.setattr(settings, "alert_quiet_to_hour", 7)
    monkeypatch.setattr(settings, "alert_digest_hour", 9)
    await a_list(database, [])
    sender = ListRecorder()

    await deliver(database, sender, now=NOW.replace(hour=3))
    assert sender.messages == []
    assert await undelivered(database) == (1, 0)

    await deliver(database, sender, now=NOW.replace(hour=7, minute=5))
    assert len(sender.messages) == 1


# --- failing, missing, and the other chat --------------------------------


async def test_a_refused_chat_fails_and_goes_nowhere_else(
    database: Database, monkeypatch: pytest.MonkeyPatch
) -> None:
    with_youtube(monkeypatch)
    await a_list(database, [])
    sender = ListRecorder(fail=True)

    await deliver(database, sender)

    assert sender.messages == []
    count, error = await attempts(database)
    assert count == 1 and error is not None and "unreachable" in error
    assert await undelivered(database) == (1, 0)


async def test_not_to_the_youtube_topics(
    database: Database, monkeypatch: pytest.MonkeyPatch
) -> None:
    with_youtube(monkeypatch)
    await a_post(database, FIRST)
    await a_list(database, [FIRST])
    async with database.session() as session:
        await store_yt_week(
            session,
            [
                YtNewList(
                    week_start=WEEK,
                    route=route,
                    computed_at=NOW,
                    threshold=2.0,
                    size=10,
                    comments_min=10,
                    videos_scored=40,
                    channels_above=0,
                    entries=(),
                )
                for route in (YtAlertRoute.EN, YtAlertRoute.RU)
            ],
        )
    sender = ListRecorder()

    await deliver(database, sender)

    telegram = [
        (t, d) for t, d in sender.messages if t.startswith("📋 Telegram")
    ]
    youtube = [
        (t, d) for t, d in sender.messages if t.startswith("📋 YouTube")
    ]
    assert len(telegram) == 1 and len(youtube) == 2
    assert all(d.chat_id == CHAT and d.topic_id is None for _, d in telegram)
    assert all(
        d.chat_id == CHAT and d.topic_id in (TOPIC_EN, TOPIC_RU)
        for _, d in youtube
    )
    assert await undelivered(database) == (0, 0)


async def test_no_alert_chat_sends_no_list(
    database: Database, monkeypatch: pytest.MonkeyPatch
) -> None:
    with_youtube(monkeypatch)
    monkeypatch.setattr(settings, "alert_chat_id", None)
    await a_post(database, FIRST)
    await a_list(database, [FIRST])
    sender = ListRecorder()

    await deliver(database, sender)

    assert sender.messages == []
    assert await undelivered(database) == (1, 1)
