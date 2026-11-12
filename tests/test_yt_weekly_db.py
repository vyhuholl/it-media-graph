"""The weekly lists' tables: storing a week, and the bot's side of them."""

import json
from datetime import UTC, date, datetime, timedelta

from sqlalchemy import text
from ytfakes import channel_id, channel_resource, video_id
from ytscoring import Video, seed_channel, seed_videos

from itgraph.db.models import YtAlertRoute, YtMetric, YtVideoFormat
from itgraph.db.session import Database
from itgraph.db.yt_weekly import (
    NewEntry,
    NewList,
    claim_lists,
    entry_evidence,
    mark_entries_delivered,
    mark_list_delivered,
    mark_list_failed,
    store_week,
)

WEEK = date(2026, 11, 16)
COMPUTED = datetime(2026, 11, 23, 7, 0, tzinfo=UTC)
PUBLISHED = datetime(2026, 11, 18, 9, 0, tzinfo=UTC)


def read_at(hours: int) -> datetime:
    return PUBLISHED + timedelta(hours=hours)


async def two_channels(database: Database) -> None:
    for number in (1, 2):
        await seed_channel(database, number, lang="ru")
    await seed_videos(
        database,
        [
            Video(
                number=number,
                channel=number,
                published=PUBLISHED,
                duration="PT45S" if number == 2 else "PT20M",
                readings=[
                    (read_at(24), 900, None, 12),
                    (read_at(48), 1500, None, 20),
                ],
            )
            for number in (1, 2)
        ],
    )


def a_list(
    *entries: NewEntry,
    week: date = WEEK,
    route: YtAlertRoute = YtAlertRoute.RU,
) -> NewList:
    return NewList(
        week_start=week,
        route=route,
        computed_at=COMPUTED,
        threshold=2.0,
        size=10,
        comments_min=10,
        videos_scored=40,
        channels_above=len(entries),
        entries=entries,
    )


def an_entry(rank: int, number: int, *, hours: int = 24) -> NewEntry:
    return NewEntry(
        rank=rank,
        channel_id=channel_id(number),
        video_id=video_id(number),
        scored_at=read_at(hours),
        metric=YtMetric.VIEWS,
        value=4.0 - rank,
        expected=200.0,
        others=rank - 1,
    )


async def count(database: Database, table: str) -> int:
    async with database.session() as session:
        return int(await session.scalar(text(f"SELECT count(*) FROM {table}")))


async def test_a_week_is_stored_whole_and_once(database: Database) -> None:
    await two_channels(database)
    week = [
        a_list(an_entry(1, 1), an_entry(2, 2)),
        a_list(route=YtAlertRoute.EN),
    ]

    async with database.session() as session:
        assert await store_week(session, week)
    async with database.session() as session:
        assert not await store_week(session, week)

    assert await count(database, "yt_weekly_lists") == 2
    assert await count(database, "yt_weekly_entries") == 2


async def test_lists_are_claimed_oldest_week_first_with_entries_in_rank(
    database: Database,
) -> None:
    await two_channels(database)
    async with database.session() as session:
        await store_week(session, [a_list(an_entry(2, 2), an_entry(1, 1))])
        await store_week(session, [a_list(week=WEEK - timedelta(days=7))])
        await store_week(
            session, [a_list(route=YtAlertRoute.EN, week=WEEK - timedelta(14))]
        )

    async with database.session() as session:
        claimed = await claim_lists(session, YtAlertRoute.RU)

    assert [found.week_start for found in claimed] == [
        WEEK - timedelta(days=7),
        WEEK,
    ]
    assert [entry.rank for entry in claimed[1].entries] == [1, 2]
    assert claimed[1].sent == 0
    assert claimed[0].entries == ()


async def test_a_sent_part_is_not_claimed_again(database: Database) -> None:
    await two_channels(database)
    async with database.session() as session:
        await store_week(session, [a_list(an_entry(1, 1), an_entry(2, 2))])
    async with database.session() as session:
        (pending,) = await claim_lists(session, YtAlertRoute.RU)
        await mark_entries_delivered(session, pending.id, [1], at=COMPUTED)
        await mark_list_failed(session, pending.id, error="boom")

    async with database.session() as session:
        (resumed,) = await claim_lists(session, YtAlertRoute.RU)

    assert [entry.rank for entry in resumed.entries] == [2]
    assert resumed.sent == 1
    assert resumed.attempts == 1

    async with database.session() as session:
        await mark_entries_delivered(session, resumed.id, [2], at=COMPUTED)
        await mark_list_delivered(session, resumed.id, at=COMPUTED)
    async with database.session() as session:
        assert await claim_lists(session, YtAlertRoute.RU) == []


async def test_an_entry_whose_video_is_gone_is_not_claimed(
    database: Database,
) -> None:
    await two_channels(database)
    async with database.session() as session:
        await store_week(session, [a_list(an_entry(1, 1), an_entry(2, 2))])
        await session.execute(
            text("DELETE FROM yt_raw_videos WHERE video_id = :id"),
            {"id": video_id(1)},
        )

    async with database.session() as session:
        (pending,) = await claim_lists(session, YtAlertRoute.RU)

    assert [entry.video_id for entry in pending.entries] == [video_id(2)]


async def test_an_entry_s_evidence_is_read_at_sending(
    database: Database,
) -> None:
    await two_channels(database)
    async with database.session() as session:
        await session.execute(
            text(
                "INSERT INTO yt_raw_channels (channel_id, payload, fetched_at) "
                "VALUES (:id, CAST(:payload AS jsonb), :at)"
            ),
            {
                "id": channel_id(2),
                "payload": json.dumps(channel_resource(2)),
                "at": COMPUTED,
            },
        )
        await store_week(session, [a_list(an_entry(1, 2))])

    async with database.session() as session:
        (pending,) = await claim_lists(session, YtAlertRoute.RU)
        evidence = await entry_evidence(
            session, pending.entries[0], short_max_seconds=180
        )

    assert evidence is not None
    assert evidence.title == "Fake Video 2"
    assert evidence.channel_title == "Fake Channel 2"
    assert evidence.format is YtVideoFormat.SHORT
    # The newest reading, with its moment; likes were never reported.
    assert evidence.counted_at == read_at(48)
    assert (evidence.counters.views, evidence.counters.likes) == (1500, None)
    # The reading the score came from.
    assert evidence.scored.views == 900
    assert evidence.scored_comments == 12
