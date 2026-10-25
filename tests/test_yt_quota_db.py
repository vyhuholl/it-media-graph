"""The quota ledger: booked before sending, never refunded, never overrun."""

import asyncio
from datetime import UTC, date, datetime, timedelta

import pytest
from sqlalchemy import func, select

from itgraph.config import settings
from itgraph.db.models import (
    YtCommand,
    YtQuotaDay,
    YtQuotaPool,
    YtRequest,
    YtRequestOutcome,
)
from itgraph.db.session import Database
from itgraph.db.yt_quota import (
    Booking,
    PoolSpentError,
    QuotaExhaustedError,
    book,
    check_not_exhausted,
    quota_report,
    record_exhaustion,
    record_outcome,
)

NOW = datetime(2026, 10, 26, 12, 0, tzinfo=UTC)


@pytest.fixture(autouse=True)
def small_quota(monkeypatch: pytest.MonkeyPatch) -> None:
    """A day of ten units, three held for the poll loop, no margin.

    Seven for the general pool: small enough to spend in a test, and the
    arithmetic is the real one.
    """
    monkeypatch.setattr(settings, "youtube_daily_quota", 10)
    monkeypatch.setattr(settings, "youtube_quota_margin", 0.0)
    monkeypatch.setattr(settings, "youtube_watch_reserve", 3)


async def _spent(database: Database) -> int:
    async with database.session() as session:
        total = await session.scalar(
            select(func.coalesce(func.sum(YtQuotaDay.spent), 0))
        )
    return int(total or 0)


async def _journal(database: Database) -> list[YtRequest]:
    async with database.session() as session:
        rows = await session.scalars(select(YtRequest).order_by(YtRequest.id))
        return list(rows)


async def _book(
    database: Database, units: int = 1, *, now: datetime = NOW
) -> Booking:
    return await book(
        database,
        command=YtCommand.ADD,
        method="channels.list",
        units=units,
        now=now,
    )


async def test_a_booking_counts_and_is_journalled(database: Database) -> None:
    booking = await _book(database, 2)

    assert await _spent(database) == 2
    [row] = await _journal(database)
    assert row.id == booking.request_id
    assert (row.command, row.method, row.units) == (
        YtCommand.ADD,
        "channels.list",
        2,
    )
    assert row.day == date(2026, 10, 26)
    assert row.pool is YtQuotaPool.GENERAL
    # Not yet answered: the outcome is unknown, not a success.
    assert row.outcome is None


async def test_a_booking_past_the_cap_is_refused(database: Database) -> None:
    await _book(database, 6)

    with pytest.raises(PoolSpentError, match="general"):
        await _book(database, 2)

    assert await _spent(database) == 6
    assert len(await _journal(database)) == 1


async def test_the_reserve_is_out_of_reach(database: Database) -> None:
    """Seven of ten are the general pool's; the other three are held for
    the poll loop even though nothing is spending them."""
    await _book(database, 7)

    with pytest.raises(PoolSpentError):
        await _book(database, 1)


async def _book_watch(database: Database, units: int = 1) -> Booking:
    return await book(
        database,
        command=YtCommand.WATCH,
        method="videos.list",
        units=units,
        now=NOW,
    )


async def test_the_poll_loop_spends_its_own_share(database: Database) -> None:
    booking = await _book_watch(database, 2)

    assert booking.pool is YtQuotaPool.WATCH
    async with database.session() as session:
        general = await session.get(
            YtQuotaDay, (date(2026, 10, 26), YtQuotaPool.GENERAL)
        )
    assert general is None


async def test_the_poll_loop_cannot_reach_the_general_pool(
    database: Database,
) -> None:
    await _book_watch(database, 3)

    with pytest.raises(PoolSpentError, match="watch"):
        await _book_watch(database, 1)

    # General still has all seven, and stays the general pool's.
    await _book(database, 7)


async def test_the_general_pool_cannot_reach_the_poll_loops(
    database: Database,
) -> None:
    await _book(database, 7)

    with pytest.raises(PoolSpentError, match="general"):
        await _book(database, 1)

    await _book_watch(database, 3)


async def test_the_refusal_says_when_spending_resumes(
    database: Database,
) -> None:
    await _book(database, 7)

    with pytest.raises(PoolSpentError) as caught:
        await _book(database, 1)

    # Midnight Pacific after 26 October 2026 is 07:00 UTC on the 27th.
    assert "2026-10-27 07:00 UTC" in str(caught.value)


async def test_units_are_never_returned(database: Database) -> None:
    """A failed request was still charged, as far as anyone can tell."""
    booking = await _book(database, 3)

    await record_outcome(database, booking, YtRequestOutcome.ERROR)

    assert await _spent(database) == 3
    [row] = await _journal(database)
    assert row.outcome is YtRequestOutcome.ERROR


async def test_a_new_quota_day_starts_from_zero(database: Database) -> None:
    await _book(database, 7)

    await _book(database, 7, now=NOW + timedelta(days=1))

    # Both days' bookings stand; neither was refused for the other's.
    assert await _spent(database) == 14


async def test_two_processes_racing_for_the_last_unit(
    database: Database, database_url: str
) -> None:
    """The conditional update, not a lock, is what makes exactly one win."""
    await _book(database, 6)
    rival = Database(database_url)
    try:
        results = await asyncio.gather(
            _book(database, 1),
            _book(rival, 1),
            return_exceptions=True,
        )
    finally:
        await rival.dispose()

    assert sum(isinstance(result, Booking) for result in results) == 1
    assert sum(isinstance(result, PoolSpentError) for result in results) == 1
    assert await _spent(database) == 7
    assert len(await _journal(database)) == 2


async def test_exhaustion_ends_the_day_for_everyone(
    database: Database,
) -> None:
    """Our count still has room; YouTube's does not, and YouTube wins."""
    await _book(database, 1)
    await record_exhaustion(database, command=YtCommand.REFRESH, now=NOW)

    with pytest.raises(QuotaExhaustedError, match="quotaExceeded"):
        await _book(database, 1)
    with pytest.raises(QuotaExhaustedError):
        await check_not_exhausted(database, now=NOW)

    assert await _spent(database) == 1


async def test_exhaustion_lasts_until_the_reset(database: Database) -> None:
    await record_exhaustion(database, command=YtCommand.ADD, now=NOW)

    after_reset = datetime(2026, 10, 27, 7, 0, tzinfo=UTC)
    await check_not_exhausted(database, now=after_reset)
    await _book(database, 1, now=after_reset)


async def test_hearing_exhaustion_twice_changes_nothing(
    database: Database,
) -> None:
    await record_exhaustion(database, command=YtCommand.ADD, now=NOW)
    await record_exhaustion(
        database, command=YtCommand.REFRESH, now=NOW + timedelta(hours=1)
    )

    async with database.session() as session:
        report = await quota_report(session, now=NOW)
    assert report.exhausted is not None
    assert report.exhausted.command is YtCommand.ADD


async def test_the_report(database: Database) -> None:
    await record_outcome(
        database, await _book(database, 1), YtRequestOutcome.OK
    )
    await _book(database, 2)  # never answered
    await book(
        database,
        command=YtCommand.REFRESH,
        method="channels.list",
        units=1,
        now=NOW,
    )

    async with database.session() as session:
        report = await quota_report(session, now=NOW)

    assert report.day == date(2026, 10, 26)
    assert report.reset == datetime(2026, 10, 27, 7, 0, tzinfo=UTC)
    general, watch = report.pools
    assert (general.pool, general.spent, general.cap) == (
        YtQuotaPool.GENERAL,
        4,
        7,
    )
    assert (watch.pool, watch.spent, watch.cap) == (YtQuotaPool.WATCH, 0, 3)
    assert report.exhausted is None
    add, refresh = report.methods
    assert (add.command, add.requests, add.units, add.unknown) == (
        YtCommand.ADD,
        2,
        3,
        1,
    )
    assert (refresh.command, refresh.units) == (YtCommand.REFRESH, 1)


async def test_an_empty_day_reports_zero(database: Database) -> None:
    async with database.session() as session:
        report = await quota_report(session, now=NOW)

    assert [(use.spent, use.cap) for use in report.pools] == [(0, 7), (0, 3)]
    assert report.methods == []
