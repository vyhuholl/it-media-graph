"""The second wind's delivery: after the list's entries, once switched on.

Telegram's list carries the cases; the loop is the same for YouTube's,
whose own section is tried once at the end.
"""

from datetime import timedelta

import pytest
from sqlalchemy import text
from test_telegram_weekly_delivery import (
    FIRST,
    NOW,
    SCORED_AT,
    WEEK,
    ListRecorder,
    a_post,
    configured,  # noqa: F401 - the autouse fixture, applied here too
    deliver,
)

from itgraph.bot.render import SECOND_WIND_NONE, SECOND_WIND_POSTS
from itgraph.config import settings
from itgraph.db.models import Metric
from itgraph.db.session import Database
from itgraph.db.weekly import NewEntry, NewList, NewRevival, store_week

SECOND = FIRST + 1
THIRD = FIRST + 2


def an_entry(rank: int, channel: int) -> NewEntry:
    return NewEntry(
        rank=rank,
        channel_id=channel,
        msg_id=10,
        scored_at=SCORED_AT,
        metric=Metric.VIEWS,
        value=4.24,
        observed=8_200.0,
        expected=2_000.0,
        others=0,
    )


def a_revival(rank: int, channel: int) -> NewRevival:
    return NewRevival(
        rank=rank,
        channel_id=channel,
        msg_id=10,
        before_at=SCORED_AT - timedelta(days=14),
        start_at=SCORED_AT - timedelta(days=7),
        end_at=SCORED_AT,
        previous_pace=100.0,
        pace=400.0,
        norm=1000.0,
        others=0,
    )


async def a_list(
    database: Database,
    *,
    entries: tuple[NewEntry, ...] = (),
    revivals: tuple[NewRevival, ...] = (),
    shown: bool = True,
) -> None:
    async with database.session() as session:
        await store_week(
            session,
            NewList(
                week_start=WEEK,
                computed_at=NOW,
                threshold=3.7,
                size=10,
                comments_min=10,
                posts_scored=3912,
                channels_above=len(entries),
                entries=entries,
                revivals=revivals,
                second_wind_shown=shown,
            ),
        )


async def undelivered_revivals(database: Database) -> int:
    async with database.session() as session:
        return int(
            await session.scalar(
                text(
                    "SELECT count(*) FROM weekly_revivals "
                    "WHERE delivered_at IS NULL"
                )
            )
        )


async def posts(database: Database, *channels: int) -> None:
    for channel in channels:
        await a_post(database, channel, username=f"fake_wind_{channel}")


async def test_switched_on_the_section_follows_the_entries(
    database: Database,
) -> None:
    await posts(database, FIRST, SECOND)
    await a_list(
        database,
        entries=(an_entry(1, FIRST),),
        revivals=(a_revival(1, SECOND),),
    )
    sender = ListRecorder()

    await deliver(database, sender)

    [(message, _)] = sender.messages
    assert message.index(
        "1. <b>A fake channel</b> (@fake_wind_7000000001)"
    ) < (message.index(SECOND_WIND_POSTS))
    assert f"{SECOND_WIND_POSTS}\n1. " in message
    assert "в 4 раза быстрее прошлой недели" in message
    assert await undelivered_revivals(database) == 0


async def test_computed_in_the_shadow_no_section_is_sent(
    database: Database, monkeypatch: pytest.MonkeyPatch
) -> None:
    """And a switch turned on after the list was computed changes nothing
    for that list."""
    monkeypatch.setattr(settings, "weekly_second_wind_enabled", True)
    await posts(database, FIRST, SECOND)
    await a_list(
        database,
        entries=(an_entry(1, FIRST),),
        revivals=(a_revival(1, SECOND),),
        shown=False,
    )
    sender = ListRecorder()

    await deliver(database, sender)

    [(message, _)] = sender.messages
    assert "Второе дыхание" not in message
    assert await undelivered_revivals(database) == 1
    async with database.session() as session:
        assert await session.scalar(
            text("SELECT delivered_at IS NOT NULL FROM weekly_lists")
        )


async def test_nothing_sped_up_is_said_in_one_line(database: Database) -> None:
    await posts(database, FIRST)
    await a_list(database, entries=(an_entry(1, FIRST),))
    sender = ListRecorder()

    await deliver(database, sender)

    [(message, _)] = sender.messages
    assert message.endswith(SECOND_WIND_NONE)


async def test_a_revival_gone_before_sending_closes_the_ranks(
    database: Database,
) -> None:
    await posts(database, FIRST, SECOND, THIRD)
    await a_list(
        database,
        entries=(an_entry(1, FIRST),),
        revivals=(a_revival(1, SECOND), a_revival(2, THIRD)),
    )
    async with database.session() as session:
        await session.execute(
            text("DELETE FROM channels WHERE tg_id = :c"), {"c": SECOND}
        )
    sender = ListRecorder()

    await deliver(database, sender)

    [(message, _)] = sender.messages
    section = message[message.index(SECOND_WIND_POSTS) :]
    assert "1. <b>A fake channel</b> (@fake_wind_7000000003)" in section
    assert "2. " not in section


async def test_an_empty_list_with_a_second_wind(database: Database) -> None:
    await posts(database, SECOND)
    await a_list(database, revivals=(a_revival(1, SECOND),))
    sender = ListRecorder()

    await deliver(database, sender)

    [(message, _)] = sender.messages
    assert "Выше порога — ничего." in message
    assert message.index("Выше порога — ничего.") < message.index(
        SECOND_WIND_POSTS
    )
    assert await undelivered_revivals(database) == 0


async def test_a_failed_part_is_resumed_and_each_section_marked(
    database: Database,
) -> None:
    """Twenty long entries and a second wind split into parts; every part
    after the first fails, and the next pass sends the rest, the second
    wind last."""
    channels = [FIRST + 10 + n for n in range(20)]
    for channel in channels:
        await a_post(
            database,
            channel,
            username=f"fake_wind_{channel}",
            title="A fake channel with a long name " * 4,
        )
    await posts(database, SECOND)
    await a_list(
        database,
        entries=tuple(
            an_entry(rank, channel)
            for rank, channel in enumerate(channels, start=1)
        ),
        revivals=(a_revival(1, SECOND),),
    )

    failing = ListRecorder(fail_on=frozenset(range(2, 10)))
    await deliver(database, failing)
    assert len(failing.messages) == 1
    assert SECOND_WIND_POSTS not in failing.messages[0][0]
    assert await undelivered_revivals(database) == 1

    resumed = ListRecorder()
    await deliver(database, resumed)
    texts = [message for message, _ in resumed.messages]
    assert "продолжение" in texts[0]
    assert SECOND_WIND_POSTS in texts[-1]
    assert await undelivered_revivals(database) == 0
