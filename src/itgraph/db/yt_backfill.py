"""Where the history walk stands with each channel, and the walk's lock.

The walk's own records, holding nothing YouTube said: when a channel was
walked, how far back, and counts of the walk's work and of what the API
did. A channel's uploads playlist is read out of ``yt_raw_channels``
when needed and never copied here.

A record is forgotten when its channel stops being a seed or goes gone,
so a channel that comes back is walked again.

The lock is the poll loop's pattern with a key of its own: a walk and a
pass never refuse each other, and neither touches the Telegram lease,
whose two-``int4`` key space the single-``bigint`` one never meets.
"""

import hashlib
from collections.abc import AsyncIterator, Iterable, Sequence
from contextlib import asynccontextmanager
from dataclasses import dataclass
from datetime import datetime

from sqlalchemy import delete, func, or_, select, text, update
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.ext.asyncio import AsyncSession, create_async_engine
from sqlalchemy.pool import NullPool

from itgraph.db.models import (
    ChannelStatus,
    YtBackfillWalk,
    YtChannel,
    YtRawChannel,
)
from itgraph.db.session import Database

__all__ = [
    "BACKFILL_LOCK_KEY",
    "WALK_ERROR_LIMIT",
    "BackfillBusyError",
    "ChannelToWalk",
    "PageCounts",
    "WalkSummary",
    "backfill_lock",
    "channels_to_walk",
    "finish_walk",
    "forget_walks",
    "record_page",
    "record_walk_failure",
    "start_walk",
    "walk_summary",
]

# One constant key, as the poll loop's: one walk per database. Hashed
# from a name of its own, so it cannot equal `WATCH_LOCK_KEY`.
BACKFILL_LOCK_KEY = int.from_bytes(
    hashlib.blake2b(b"itgraph.yt-backfill").digest()[:8], "big", signed=True
)

# How much of a failing walk's error is kept: enough for a status line.
WALK_ERROR_LIMIT = 200


class BackfillBusyError(RuntimeError):
    """Another walk holds the history walk's lock."""


@asynccontextmanager
async def backfill_lock(database: Database) -> AsyncIterator[None]:
    """Hold the walk's lock for the duration, or raise ``BackfillBusyError``.

    On a connection of its own, outside any pool, as the poll loop's lock
    is and for its reason: an advisory lock belongs to the database
    session that took it. A killed walk releases it with its connection.
    """
    engine = create_async_engine(database.engine.url, poolclass=NullPool)
    try:
        async with engine.connect() as connection:
            taken = await connection.scalar(
                text("SELECT pg_try_advisory_lock(:key)"),
                {"key": BACKFILL_LOCK_KEY},
            )
            await connection.commit()
            if not taken:
                raise BackfillBusyError(
                    "another `yt backfill` walk is running; this one makes "
                    "no request"
                )
            try:
                yield
            finally:
                await connection.execute(
                    text("SELECT pg_advisory_unlock(:key)"),
                    {"key": BACKFILL_LOCK_KEY},
                )
                await connection.commit()
    finally:
        await engine.dispose()


@dataclass(frozen=True, slots=True)
class ChannelToWalk:
    """A channel this walk lists, and the playlist it lists, if known."""

    channel_id: str
    uploads: str | None


async def channels_to_walk(
    session: AsyncSession, *, named: Sequence[str] | None = None
) -> list[ChannelToWalk]:
    """The seeds this walk lists, in a stable order.

    Without names: every seed not gone whose walk never began or did not
    end. With names: those of them that are seeds and not gone, walked
    or not — naming a channel is asking for it again. The playlist comes
    from the stored payload and is ``None`` where there is none.
    """
    uploads = YtRawChannel.payload["contentDetails"]["relatedPlaylists"][
        "uploads"
    ].astext
    statement = (
        select(YtChannel.channel_id, uploads)
        .outerjoin(
            YtRawChannel, YtRawChannel.channel_id == YtChannel.channel_id
        )
        .outerjoin(
            YtBackfillWalk, YtBackfillWalk.channel_id == YtChannel.channel_id
        )
        .where(
            YtChannel.status == ChannelStatus.SEED,
            YtChannel.gone_at.is_(None),
        )
        .order_by(YtChannel.channel_id)
    )
    if named is None:
        statement = statement.where(
            or_(
                YtBackfillWalk.channel_id.is_(None),
                YtBackfillWalk.finished_at.is_(None),
            )
        )
    else:
        statement = statement.where(YtChannel.channel_id.in_(list(named)))
    rows = await session.execute(statement)
    return [
        ChannelToWalk(channel_id=channel_id, uploads=playlist)
        for channel_id, playlist in rows.tuples()
    ]


async def start_walk(
    session: AsyncSession,
    channel_id: str,
    *,
    now: datetime,
    cutoff: datetime,
    whole: bool,
) -> None:
    """Begin a channel's walk from its newest page, counters at zero.

    A failure count carries over until a walk ends: a channel failing day
    after day says so in the status.
    """
    fresh = {
        "cutoff": cutoff,
        "started_at": now,
        "finished_at": None,
        "whole": whole,
        "pages": 0,
        "videos": 0,
        "out_of_order": 0,
        "undated": 0,
        "unreturned": 0,
        "capped": False,
    }
    statement = insert(YtBackfillWalk).values(channel_id=channel_id, **fresh)
    await session.execute(
        statement.on_conflict_do_update(
            index_elements=[YtBackfillWalk.channel_id], set_=fresh
        )
    )


@dataclass(frozen=True, slots=True)
class PageCounts:
    """What one page of a walk adds to its record."""

    videos: int = 0
    out_of_order: int = 0
    undated: int = 0
    unreturned: int = 0


async def record_page(
    session: AsyncSession, channel_id: str, counts: PageCounts
) -> None:
    """Add one listed page to a walk's record.

    Called in the transaction that stores the page's videos, so the
    record never counts work that was not kept.
    """
    walk = YtBackfillWalk
    await session.execute(
        update(walk)
        .where(walk.channel_id == channel_id)
        .values(
            pages=walk.pages + 1,
            videos=walk.videos + counts.videos,
            out_of_order=walk.out_of_order + counts.out_of_order,
            undated=walk.undated + counts.undated,
            unreturned=walk.unreturned + counts.unreturned,
        )
        .execution_options(synchronize_session=False)
    )


async def finish_walk(
    session: AsyncSession, channel_id: str, *, now: datetime, capped: bool
) -> None:
    """A channel's walk reached its end: the depth, the playlist, or the cap."""
    await session.execute(
        update(YtBackfillWalk)
        .where(YtBackfillWalk.channel_id == channel_id)
        .values(
            finished_at=now,
            capped=capped,
            consecutive_failures=0,
            last_error=None,
        )
        .execution_options(synchronize_session=False)
    )


async def record_walk_failure(
    session: AsyncSession, channel_id: str, *, error: str
) -> None:
    """A channel's uploads could not be listed: it stays due, and counted."""
    await session.execute(
        update(YtBackfillWalk)
        .where(YtBackfillWalk.channel_id == channel_id)
        .values(
            consecutive_failures=YtBackfillWalk.consecutive_failures + 1,
            last_error=error[:WALK_ERROR_LIMIT],
        )
        .execution_options(synchronize_session=False)
    )


async def forget_walks(
    session: AsyncSession, channel_ids: Iterable[str]
) -> None:
    """Forget these channels' walks; each is walked anew when next due."""
    ids = list(dict.fromkeys(channel_ids))
    if ids:
        await session.execute(
            delete(YtBackfillWalk).where(YtBackfillWalk.channel_id.in_(ids))
        )


@dataclass(frozen=True, slots=True)
class WalkSummary:
    """Where the walk stands over the seeds, for the status report."""

    seeds: int
    walked: int
    due: int
    interrupted: int
    failing: int
    capped: int
    out_of_order: int
    undated: int
    unreturned: int


async def walk_summary(session: AsyncSession) -> WalkSummary:
    """Seeds walked, due, interrupted, failing and capped; the API's counts.

    Over seeds not gone, which are the channels a walk would list.
    """
    walk = YtBackfillWalk
    row = (
        await session.execute(
            select(
                func.count(),
                func.count(walk.finished_at),
                func.count().filter(walk.finished_at.is_(None)),
                func.count().filter(
                    walk.channel_id.is_not(None), walk.finished_at.is_(None)
                ),
                func.count().filter(walk.consecutive_failures > 0),
                func.count().filter(walk.capped),
                func.coalesce(func.sum(walk.out_of_order), 0),
                func.coalesce(func.sum(walk.undated), 0),
                func.coalesce(func.sum(walk.unreturned), 0),
            )
            .select_from(YtChannel)
            .outerjoin(walk, walk.channel_id == YtChannel.channel_id)
            .where(
                YtChannel.status == ChannelStatus.SEED,
                YtChannel.gone_at.is_(None),
            )
        )
    ).one()
    return WalkSummary(
        seeds=row[0],
        walked=row[1],
        due=row[2],
        interrupted=row[3],
        failing=row[4],
        capped=row[5],
        out_of_order=row[6],
        undated=row[7],
        unreturned=row[8],
    )
