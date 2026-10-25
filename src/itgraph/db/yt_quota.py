"""The YouTube quota ledger: booking units, and what came of them.

Every YouTube request is booked here before it is sent, in a transaction
of its own that commits first. That ordering is the whole design: a
request can never have been paid for without a row saying so, and the
conditional ``UPDATE`` that books it is what keeps two processes inside a
cap without a lock — the second one's ``WHERE`` is re-read against the
first one's committed count.

Units are never returned. YouTube charges invalid requests too, and a
request that timed out may or may not have been counted, so the ledger
counts it. Overcounting costs headroom; undercounting costs the day.
"""

from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from datetime import UTC, date, datetime

from sqlalchemy import case, func, select, update
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.ext.asyncio import AsyncSession

from itgraph.config import settings
from itgraph.db.models import (
    YtCommand,
    YtQuotaDay,
    YtQuotaExhausted,
    YtQuotaPool,
    YtRequest,
    YtRequestOutcome,
)
from itgraph.db.session import Database
from itgraph.yt_quota import general_cap, next_reset, quota_day

__all__ = [
    "POOL_OF",
    "Booking",
    "MethodUse",
    "PoolSpentError",
    "PoolUse",
    "QuotaError",
    "QuotaExhaustedError",
    "QuotaReport",
    "book",
    "check_not_exhausted",
    "describe_reset",
    "pool_cap",
    "pool_spent",
    "quota_report",
    "record_exhaustion",
    "record_outcome",
]


def describe_reset(reset: datetime) -> str:
    """A reset moment as the operator reads it: in UTC, and on this clock."""
    local = reset.astimezone()
    return f"{reset:%Y-%m-%d %H:%M} UTC ({local:%H:%M %Z} here)"


class QuotaError(RuntimeError):
    """A request the ledger will not let through.

    The message is the operator's sentence, not a diagnosis: it says
    what is spent and when spending can resume.
    """


class PoolSpentError(QuotaError):
    """The pool's own cap for the day is reached."""

    def __init__(self, pool: YtQuotaPool, cap: int, reset: datetime) -> None:
        super().__init__(
            f"the {pool.value} YouTube quota pool is spent for today "
            f"({cap} units); the next quota day begins "
            f"{describe_reset(reset)}"
        )
        self.pool = pool
        self.reset = reset


class QuotaExhaustedError(QuotaError):
    """YouTube itself answered ``quotaExceeded`` today.

    Whatever our counts say: they decide who may spend what, Google's
    decides whether anyone can.
    """

    def __init__(self, reset: datetime) -> None:
        super().__init__(
            "YouTube answered quotaExceeded today, so no YouTube request "
            "is made until the next quota day begins "
            f"{describe_reset(reset)}"
        )
        self.reset = reset


# Which pool each command spends from. A table rather than a parameter a
# caller passes, so no call site can book the poll loop's work against
# the general pool, or anything else against the loop's reserve. Neither
# borrows from the other: the caps are settings, and borrowing would turn
# a misjudged setting into an invisible one.
POOL_OF: Mapping[YtCommand, YtQuotaPool] = {
    YtCommand.ADD: YtQuotaPool.GENERAL,
    YtCommand.REFRESH: YtQuotaPool.GENERAL,
    YtCommand.WATCH: YtQuotaPool.WATCH,
}


def pool_cap(pool: YtQuotaPool) -> int:
    """What a pool may book in one quota day.

    The poll loop's is exactly the reserve the general cap leaves out.
    """
    match pool:
        case YtQuotaPool.GENERAL:
            return general_cap(
                settings.youtube_daily_quota,
                settings.youtube_quota_margin,
                settings.youtube_watch_reserve,
            )
        case YtQuotaPool.WATCH:
            return settings.youtube_watch_reserve


async def pool_spent(
    database: Database, pool: YtQuotaPool, *, now: datetime | None = None
) -> int:
    """What a pool has booked so far in the current quota day."""
    moment = now or datetime.now(UTC)
    async with database.session() as session:
        spent = await session.scalar(
            select(YtQuotaDay.spent).where(
                YtQuotaDay.day == quota_day(moment), YtQuotaDay.pool == pool
            )
        )
    return spent or 0


@dataclass(frozen=True, slots=True)
class Booking:
    """Units booked for one request, and the journal row that says so."""

    request_id: int
    day: date
    pool: YtQuotaPool
    units: int


async def check_not_exhausted(
    database: Database, *, now: datetime | None = None
) -> None:
    """Refuse to start while today is a day YouTube declared over.

    The same check a booking makes, asked before a command begins, so a
    command started after exhaustion says so before it reads its input
    rather than at its first request.
    """
    moment = now or datetime.now(UTC)
    async with database.session() as session:
        if await session.get(YtQuotaExhausted, quota_day(moment)):
            raise QuotaExhaustedError(next_reset(moment))


async def book(
    database: Database,
    *,
    command: YtCommand,
    method: str,
    units: int,
    now: datetime | None = None,
) -> Booking:
    """Book a request's units, or refuse it. Commits before returning.

    The pool is the command's, from ``POOL_OF``. Raises
    ``QuotaExhaustedError`` on a day YouTube declared over, and
    ``PoolSpentError`` when the units would carry the pool past its cap.
    Either way nothing is written and the request must not be sent.
    """
    moment = now or datetime.now(UTC)
    day = quota_day(moment)
    pool = POOL_OF[command]
    cap = pool_cap(pool)
    async with database.session() as session:
        if await session.get(YtQuotaExhausted, day) is not None:
            raise QuotaExhaustedError(next_reset(moment))

        await session.execute(
            insert(YtQuotaDay)
            .values(day=day, pool=pool)
            .on_conflict_do_nothing()
        )
        spent = await session.scalar(
            update(YtQuotaDay)
            .where(
                YtQuotaDay.day == day,
                YtQuotaDay.pool == pool,
                YtQuotaDay.spent + units <= cap,
            )
            .values(spent=YtQuotaDay.spent + units)
            .returning(YtQuotaDay.spent)
            .execution_options(synchronize_session=False)
        )
        if spent is None:
            raise PoolSpentError(pool, cap, next_reset(moment))

        request_id = await session.scalar(
            insert(YtRequest)
            .values(
                day=day,
                pool=pool,
                command=command,
                method=method,
                units=units,
            )
            .returning(YtRequest.id)
        )
    assert request_id is not None
    return Booking(request_id=request_id, day=day, pool=pool, units=units)


async def record_outcome(
    database: Database, booking: Booking, outcome: YtRequestOutcome
) -> None:
    """Say what came of a booked request. The units stay booked."""
    async with database.session() as session:
        await session.execute(
            update(YtRequest)
            .where(YtRequest.id == booking.request_id)
            .values(outcome=outcome)
            .execution_options(synchronize_session=False)
        )


async def record_exhaustion(
    database: Database, *, command: YtCommand, now: datetime | None = None
) -> None:
    """End the quota day for every pool, as YouTube just did.

    The first command to hear it is the one recorded; a second hearing
    the same day changes nothing.
    """
    moment = now or datetime.now(UTC)
    async with database.session() as session:
        await session.execute(
            insert(YtQuotaExhausted)
            .values(day=quota_day(moment), observed_at=moment, command=command)
            .on_conflict_do_nothing()
        )


@dataclass(frozen=True, slots=True)
class PoolUse:
    """One pool's day: booked against its cap."""

    pool: YtQuotaPool
    spent: int
    cap: int


@dataclass(frozen=True, slots=True)
class MethodUse:
    """What one command spent on one method in a day.

    ``unknown`` counts requests whose outcome was never learned — a
    process killed between booking and answer — so a reader can tell a
    quiet day from one that kept dying.
    """

    command: YtCommand
    method: str
    requests: int
    units: int
    unknown: int


@dataclass(frozen=True, slots=True)
class QuotaReport:
    """One quota day, as the operator asks about it."""

    day: date
    reset: datetime
    pools: Sequence[PoolUse]
    exhausted: YtQuotaExhausted | None
    methods: Sequence[MethodUse]


async def quota_report(
    session: AsyncSession, *, now: datetime | None = None
) -> QuotaReport:
    """The current quota day: per pool, per command and method."""
    moment = now or datetime.now(UTC)
    day = quota_day(moment)

    booked = await session.execute(
        select(YtQuotaDay.pool, YtQuotaDay.spent).where(YtQuotaDay.day == day)
    )
    spent = {pool: units for pool, units in booked.tuples()}
    pools = [
        PoolUse(pool=pool, spent=spent.get(pool, 0), cap=pool_cap(pool))
        for pool in YtQuotaPool
    ]

    unknown = func.sum(case((YtRequest.outcome.is_(None), 1), else_=0))
    rows = await session.execute(
        select(
            YtRequest.command,
            YtRequest.method,
            func.count(),
            func.sum(YtRequest.units),
            unknown,
        )
        .where(YtRequest.day == day)
        .group_by(YtRequest.command, YtRequest.method)
        .order_by(YtRequest.command, YtRequest.method)
    )
    methods = [
        MethodUse(
            command=command,
            method=method,
            requests=requests,
            units=units,
            unknown=lost,
        )
        for command, method, requests, units, lost in rows.tuples()
    ]

    return QuotaReport(
        day=day,
        reset=next_reset(moment),
        pools=pools,
        exhausted=await session.get(YtQuotaExhausted, day),
        methods=methods,
    )
