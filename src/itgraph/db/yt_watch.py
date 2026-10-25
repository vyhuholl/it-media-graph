"""Where the YouTube poll loop stands: channels, passes, and the lock.

The loop's own records, holding nothing YouTube said. What a channel
reports — its video count, its uploads playlist — is read out of
``yt_raw_channels`` when needed and never copied here.

Three things live here:

- **Channel polls.** Whether a channel's uploads are owed a listing,
  when they were last listed, and how a failing one backs off. The
  arithmetic is ``yt_schedule``'s; this module asks the questions and
  writes the answers down.
- **Passes.** One row per pass, because a pass is a oneshot under a
  timer and there is no running process to ask what it last did.
- **The lock.** One pass at a time, on a Postgres advisory lock of its
  own — the single-``bigint`` form, whose key space the documentation
  says does not overlap the two-``int4`` form Telegram's session lease
  uses. So a pass and a Telegram command never refuse each other.
"""

import hashlib
import logging
from collections.abc import AsyncIterator, Iterable, Sequence
from contextlib import asynccontextmanager
from dataclasses import dataclass
from datetime import datetime

from sqlalchemy import func, select, text
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.ext.asyncio import AsyncSession, create_async_engine
from sqlalchemy.pool import NullPool

from itgraph.db.models import (
    ChannelStatus,
    YtChannel,
    YtChannelPoll,
    YtDiscoveryPath,
    YtPassOutcome,
    YtRawChannel,
    YtWatchPass,
)
from itgraph.db.session import Database
from itgraph.yt_schedule import check_due, listing_retry_at

__all__ = [
    "LISTING_ERROR_LIMIT",
    "WATCH_LOCK_KEY",
    "ChannelToList",
    "PassRecord",
    "WatchBusyError",
    "WatchedChannel",
    "channels_to_list",
    "failing_channels",
    "last_pass",
    "owe_listings",
    "record_listing_failure",
    "record_listings",
    "record_pass",
    "stored_video_counts",
    "watch_lock",
    "watched_channels",
]

logger = logging.getLogger(__name__)

# One constant key: there is one poll loop per database. Hashed from a
# name so it says what it is in a code review; truncated to a signed
# 64-bit value, which is what `pg_try_advisory_lock(bigint)` takes.
WATCH_LOCK_KEY = int.from_bytes(
    hashlib.blake2b(b"itgraph.yt-watch").digest()[:8], "big", signed=True
)

# How much of a failing listing's error is kept: enough for a status
# line, not a stack trace.
LISTING_ERROR_LIMIT = 200


class WatchBusyError(RuntimeError):
    """Another pass holds the poll loop's lock."""


@asynccontextmanager
async def watch_lock(database: Database) -> AsyncIterator[None]:
    """Hold the poll loop's lock for the duration, or raise ``WatchBusyError``.

    On a connection of its own, outside any pool, for the reason the
    session lease gives: an advisory lock belongs to the database session
    that took it, and a pooled connection handed back between statements
    would take the lock with it. A killed pass releases it by dropping
    the connection; there is nothing to clean up by hand.
    """
    engine = create_async_engine(database.engine.url, poolclass=NullPool)
    try:
        async with engine.connect() as connection:
            taken = await connection.scalar(
                text("SELECT pg_try_advisory_lock(:key)"),
                {"key": WATCH_LOCK_KEY},
            )
            # Session-level: survives the commit, which leaves the
            # connection idle rather than idle in a transaction.
            await connection.commit()
            if not taken:
                raise WatchBusyError(
                    "another `yt watch` pass is running; this one makes no "
                    "request"
                )
            try:
                yield
            finally:
                await connection.execute(
                    text("SELECT pg_advisory_unlock(:key)"),
                    {"key": WATCH_LOCK_KEY},
                )
                await connection.commit()
    finally:
        await engine.dispose()


@dataclass(frozen=True, slots=True)
class WatchedChannel:
    """A seed channel, and whether YouTube still returns it."""

    channel_id: str
    gone: bool


async def watched_channels(session: AsyncSession) -> Sequence[WatchedChannel]:
    """Every seed, gone ones included — asked for so a return is noticed."""
    rows = await session.execute(
        select(YtChannel.channel_id, YtChannel.gone_at)
        .where(YtChannel.status == ChannelStatus.SEED)
        .order_by(YtChannel.channel_id)
    )
    return [
        WatchedChannel(channel_id=channel_id, gone=gone_at is not None)
        for channel_id, gone_at in rows.tuples()
    ]


async def stored_video_counts(
    session: AsyncSession, channel_ids: Iterable[str]
) -> dict[str, str | None]:
    """Each channel's video count as last stored, before it is replaced.

    The count stays in the payload, as YouTube sent it — a string — and
    is compared as one: a count is a trigger here, not a number anyone
    does arithmetic on. A channel with no stored payload is absent.
    """
    wanted = list(channel_ids)
    if not wanted:
        return {}
    count = YtRawChannel.payload["statistics"]["videoCount"].astext
    rows = await session.execute(
        select(YtRawChannel.channel_id, count).where(
            YtRawChannel.channel_id.in_(wanted)
        )
    )
    return {channel_id: value for channel_id, value in rows.tuples()}


async def owe_listings(
    session: AsyncSession, channel_ids: Iterable[str]
) -> None:
    """Mark these channels' uploads as owed a listing.

    Called in the transaction that replaces their payloads: once the new
    count is stored, this flag is the only memory that it moved.
    """
    ids = list(dict.fromkeys(channel_ids))
    if not ids:
        return
    statement = insert(YtChannelPoll).values(
        [{"channel_id": channel_id, "list_owed": True} for channel_id in ids]
    )
    await session.execute(
        statement.on_conflict_do_update(
            index_elements=[YtChannelPoll.channel_id],
            set_={"list_owed": True},
        )
    )


@dataclass(frozen=True, slots=True)
class ChannelToList:
    """A channel whose uploads this pass lists, and why."""

    channel_id: str
    uploads: str | None
    reason: YtDiscoveryPath


async def channels_to_list(
    session: AsyncSession, *, now: datetime, checks_allowed: bool
) -> tuple[list[ChannelToList], int]:
    """What this pass lists, and how many daily checks it deferred.

    First listings and owed ones always; daily checks only when
    ``checks_allowed``. A channel failing to list waits out its backoff
    whatever it is owed, and a gone one is not listed at all. The uploads
    playlist comes from the stored payload — the detection that just ran
    replaced it — and is ``None`` where the payload lacks one.
    """
    uploads = YtRawChannel.payload["contentDetails"]["relatedPlaylists"][
        "uploads"
    ].astext
    rows = await session.execute(
        select(
            YtChannel.channel_id,
            uploads,
            YtChannelPoll.listed_at,
            YtChannelPoll.list_owed,
            YtChannelPoll.attempted_at,
            YtChannelPoll.consecutive_failures,
        )
        .outerjoin(
            YtRawChannel, YtRawChannel.channel_id == YtChannel.channel_id
        )
        .outerjoin(
            YtChannelPoll, YtChannelPoll.channel_id == YtChannel.channel_id
        )
        .where(
            YtChannel.status == ChannelStatus.SEED,
            YtChannel.gone_at.is_(None),
        )
        .order_by(YtChannel.channel_id)
    )

    listing: list[ChannelToList] = []
    deferred = 0
    for row in rows:
        failures = row.consecutive_failures or 0
        if (
            failures
            and row.attempted_at is not None
            and now < listing_retry_at(row.attempted_at, failures)
        ):
            continue
        if row.listed_at is None:
            reason = YtDiscoveryPath.FIRST
        elif row.list_owed:
            reason = YtDiscoveryPath.COUNT
        elif check_due(row.listed_at, now):
            if not checks_allowed:
                deferred += 1
                continue
            reason = YtDiscoveryPath.CHECK
        else:
            continue
        listing.append(
            ChannelToList(
                channel_id=row.channel_id, uploads=row[1], reason=reason
            )
        )
    return listing, deferred


async def record_listings(
    session: AsyncSession, channel_ids: Iterable[str], *, now: datetime
) -> None:
    """These channels were listed and what the listing revealed is stored.

    Only then is the debt cleared, so a pass that dies between listing
    and storing lists the channel again next time.
    """
    ids = list(dict.fromkeys(channel_ids))
    if not ids:
        return
    statement = insert(YtChannelPoll).values(
        [
            {
                "channel_id": channel_id,
                "listed_at": now,
                "attempted_at": now,
                "list_owed": False,
                "consecutive_failures": 0,
            }
            for channel_id in ids
        ]
    )
    await session.execute(
        statement.on_conflict_do_update(
            index_elements=[YtChannelPoll.channel_id],
            set_={
                "listed_at": now,
                "attempted_at": now,
                "list_owed": False,
                "consecutive_failures": 0,
                "last_error": None,
            },
        )
    )


async def record_listing_failure(
    session: AsyncSession, channel_id: str, *, now: datetime, error: str
) -> None:
    """A listing failed: count it, keep the debt, start the backoff."""
    trimmed = error[:LISTING_ERROR_LIMIT]
    statement = insert(YtChannelPoll).values(
        channel_id=channel_id,
        attempted_at=now,
        consecutive_failures=1,
        last_error=trimmed,
    )
    await session.execute(
        statement.on_conflict_do_update(
            index_elements=[YtChannelPoll.channel_id],
            set_={
                "attempted_at": now,
                "consecutive_failures": YtChannelPoll.consecutive_failures + 1,
                "last_error": trimmed,
            },
        )
    )


@dataclass(slots=True)
class PassRecord:
    """What one pass did, as it goes. Written once, at the end."""

    started_at: datetime
    outcome: YtPassOutcome = YtPassOutcome.COMPLETE
    channels: int = 0
    listed: int = 0
    list_failures: int = 0
    new_videos: int = 0
    readings: int = 0
    removed: int = 0
    deferred_checks: int = 0
    deferred_readings: int = 0
    error: str | None = None

    def line(self) -> str:
        """The pass in one line, as the command prints it."""
        parts = [
            f"{self.outcome.value}: {self.channels} channels",
            f"listed {self.listed}",
            f"new {self.new_videos}",
            f"readings {self.readings}",
            f"removed {self.removed}",
        ]
        if self.list_failures:
            parts.append(f"listing failures {self.list_failures}")
        if self.deferred_checks or self.deferred_readings:
            parts.append(
                f"deferred {self.deferred_checks} checks, "
                f"{self.deferred_readings} readings"
            )
        return ", ".join(parts)


async def record_pass(
    session: AsyncSession, record: PassRecord, *, finished_at: datetime
) -> None:
    session.add(
        YtWatchPass(
            started_at=record.started_at,
            finished_at=finished_at,
            outcome=record.outcome,
            channels=record.channels,
            listed=record.listed,
            list_failures=record.list_failures,
            new_videos=record.new_videos,
            readings=record.readings,
            removed=record.removed,
            deferred_checks=record.deferred_checks,
            deferred_readings=record.deferred_readings,
            error=record.error,
        )
    )


async def last_pass(session: AsyncSession) -> YtWatchPass | None:
    """The most recent pass, or ``None`` if none has run."""
    found: YtWatchPass | None = await session.scalar(
        select(YtWatchPass).order_by(YtWatchPass.started_at.desc()).limit(1)
    )
    return found


async def failing_channels(session: AsyncSession) -> int:
    """How many watched channels' last listing failed."""
    return (
        await session.scalar(
            select(func.count())
            .select_from(YtChannelPoll)
            .join(YtChannel, YtChannel.channel_id == YtChannelPoll.channel_id)
            .where(
                YtChannel.status == ChannelStatus.SEED,
                YtChannelPoll.consecutive_failures > 0,
            )
        )
        or 0
    )
