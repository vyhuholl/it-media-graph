"""Telegram's weekly lists' tables: storing a week, and the bot's side."""

from datetime import UTC, date, datetime, timedelta

import pytest
from sqlalchemy import text
from sqlalchemy.exc import IntegrityError

from itgraph.db.models import Metric
from itgraph.db.session import Database
from itgraph.db.weekly import (
    NewEntry,
    NewList,
    claim_lists,
    mark_entries_delivered,
    mark_list_delivered,
    mark_list_failed,
    store_week,
)

WEEK = date(2026, 11, 16)
COMPUTED = datetime(2026, 11, 23, 7, 0, tzinfo=UTC)
PUBLISHED = datetime(2026, 11, 18, 9, 0, tzinfo=UTC)
FIRST = 5000000001
SECOND = 5000000002


def read_at(hours: int) -> datetime:
    return PUBLISHED + timedelta(hours=hours)


async def two_channels(database: Database) -> None:
    async with database.session() as session:
        for channel, username in ((FIRST, "fake_weekly_one"), (SECOND, None)):
            await session.execute(
                text(
                    "INSERT INTO channels (tg_id, username, title, "
                    "discovered_via, status, kind) VALUES "
                    "(:id, :name, :title, 'manual', 'seed', 'personal')"
                ),
                {"id": channel, "name": username, "title": f"Fake {channel}"},
            )
            await session.execute(
                text(
                    "INSERT INTO raw_messages (channel_id, msg_id, payload) "
                    "VALUES (:c, 10, CAST(:p AS jsonb))"
                ),
                {
                    "c": channel,
                    "p": (
                        '{"_": "Message", "id": 10, '
                        f'"date": "{PUBLISHED.isoformat()}", '
                        '"message": "A first line\\nand a second"}'
                    ),
                },
            )
            for hours in (2, 8):
                await session.execute(
                    text(
                        "INSERT INTO message_metrics (channel_id, msg_id, "
                        "observed_at, views) VALUES (:c, 10, :at, 100)"
                    ),
                    {"c": channel, "at": read_at(hours)},
                )


def a_list(*entries: NewEntry, week: date = WEEK) -> NewList:
    return NewList(
        week_start=week,
        computed_at=COMPUTED,
        threshold=3.7,
        size=10,
        comments_min=10,
        posts_scored=400,
        channels_above=len(entries),
        entries=entries,
    )


def an_entry(rank: int, channel: int, *, hours: int = 8) -> NewEntry:
    return NewEntry(
        rank=rank,
        channel_id=channel,
        msg_id=10,
        scored_at=read_at(hours),
        metric=Metric.VIEWS,
        value=5.0 - rank,
        observed=900.0,
        expected=200.0,
        others=rank - 1,
    )


async def count(database: Database, table: str) -> int:
    async with database.session() as session:
        return int(await session.scalar(text(f"SELECT count(*) FROM {table}")))


async def test_a_week_is_stored_whole_and_once(database: Database) -> None:
    await two_channels(database)
    week = a_list(an_entry(1, FIRST), an_entry(2, SECOND))

    async with database.session() as session:
        assert await store_week(session, week)
    async with database.session() as session:
        assert not await store_week(session, week)

    assert await count(database, "weekly_lists") == 1
    assert await count(database, "weekly_entries") == 2


async def test_lists_are_claimed_oldest_week_first_with_entries_in_rank(
    database: Database,
) -> None:
    await two_channels(database)
    async with database.session() as session:
        await store_week(
            session, a_list(an_entry(2, SECOND), an_entry(1, FIRST))
        )
        await store_week(session, a_list(week=WEEK - timedelta(days=7)))

    async with database.session() as session:
        claimed = await claim_lists(session)

    assert [found.week_start for found in claimed] == [
        WEEK - timedelta(days=7),
        WEEK,
    ]
    assert [entry.rank for entry in claimed[1].entries] == [1, 2]
    assert claimed[1].entries[0].observed == 900.0
    assert claimed[1].sent == 0
    assert claimed[0].entries == ()


async def test_a_sent_part_is_not_claimed_again(database: Database) -> None:
    await two_channels(database)
    async with database.session() as session:
        await store_week(
            session, a_list(an_entry(1, FIRST), an_entry(2, SECOND))
        )
    async with database.session() as session:
        [found] = await claim_lists(session)
        await mark_entries_delivered(session, found.id, [1], at=COMPUTED)
        await mark_list_failed(session, found.id, error="refused")

    async with database.session() as session:
        [again] = await claim_lists(session)
    assert ([entry.rank for entry in again.entries], again.sent) == ([2], 1)
    assert again.attempts == 1

    async with database.session() as session:
        await mark_entries_delivered(session, again.id, [2], at=COMPUTED)
        await mark_list_delivered(session, again.id, at=COMPUTED)
    async with database.session() as session:
        assert await claim_lists(session) == []


async def test_an_entry_whose_reading_is_gone_is_not_claimed(
    database: Database,
) -> None:
    await two_channels(database)
    async with database.session() as session:
        await store_week(
            session, a_list(an_entry(1, FIRST), an_entry(2, SECOND))
        )
    async with database.session() as session:
        await session.execute(
            text("DELETE FROM channels WHERE tg_id = :c"), {"c": FIRST}
        )

    async with database.session() as session:
        [found] = await claim_lists(session)
    assert [entry.channel_id for entry in found.entries] == [SECOND]


async def test_a_second_run_at_once_is_told_the_week_is_stored(
    database: Database, monkeypatch: pytest.MonkeyPatch
) -> None:
    from itgraph.db import weekly

    await two_channels(database)
    week = a_list(an_entry(1, FIRST))
    async with database.session() as session:
        assert await store_week(session, week)

    async def nothing_yet(*args: object) -> bool:
        return False

    monkeypatch.setattr(weekly, "week_has_list", nothing_yet)
    async with database.session() as session:
        assert not await store_week(session, week)

    assert await count(database, "weekly_lists") == 1
    assert await count(database, "weekly_entries") == 1


async def test_any_other_refusal_is_raised(database: Database) -> None:
    """An entry naming a reading that is not stored is no answer."""
    await two_channels(database)
    broken = a_list(an_entry(1, FIRST, hours=3))

    with pytest.raises(IntegrityError):
        async with database.session() as session:
            await store_week(session, broken)

    assert await count(database, "weekly_lists") == 0
