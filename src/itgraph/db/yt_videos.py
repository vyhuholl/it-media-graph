"""The poll loop's videos: payloads, readings, and when each is due.

Everything here is API data or worked out from it, so everything here
expires. A video's row holds its latest payload and the schedule the
loop derived from it; its readings hang off that row and go with it.
Nothing about a video lives anywhere else, which is what lets one
``DELETE`` — expiry, or a video YouTube no longer returns — be the whole
of forgetting it.

Nothing here decides anything. When a video is due is computed in
``yt_schedule`` and written down here; what a payload means is read in
``derive/yt_videos``.
"""

from collections.abc import Iterable, Sequence
from dataclasses import dataclass
from datetime import UTC, datetime, time
from typing import Any

from sqlalchemy import (
    ColumnElement,
    DateTime,
    and_,
    delete,
    func,
    literal,
    not_,
    select,
    update,
)
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.ext.asyncio import AsyncSession

from itgraph import yt_policy
from itgraph.db.models import (
    ChannelStatus,
    YtChannel,
    YtDiscoveryPath,
    YtRawVideo,
    YtRawVideoStat,
)
from itgraph.derive.yt_videos import Counters

__all__ = [
    "DueVideo",
    "VideoWrite",
    "count_due",
    "count_tracked",
    "discoveries_since",
    "due_videos",
    "finish_videos",
    "known_video_ids",
    "oldest_due",
    "purge_expired_videos",
    "remove_videos",
    "replace_payloads",
    "store_videos",
    "videos_due_for_refresh",
]


@dataclass(frozen=True, slots=True)
class DueVideo:
    """A tracked video whose next reading or check has come round.

    Plain values, for the reason ``db/poll.py`` gives: the pass commits
    between requests, and a mapped instance read after a commit is a
    lazy load an async session cannot make.
    """

    video_id: str
    payload: dict[str, Any]
    fetched_at: datetime
    discovered_at: datetime
    discovered_via: YtDiscoveryPath


@dataclass(frozen=True, slots=True)
class VideoWrite:
    """What one answer says about one video, ready to be stored.

    ``reading`` is ``None`` for a broadcast that has not started: its
    payload is kept current, but there is nothing yet to read.
    ``discovered_via`` matters only the first time a video is stored and
    is never overwritten after.
    """

    video_id: str
    payload: dict[str, Any]
    due_at: datetime | None
    reading: Counters | None
    discovered_via: YtDiscoveryPath


def _watched() -> ColumnElement[bool]:
    """Video rows whose channel is a seed — the only ones the loop reads."""
    return YtRawVideo.channel_id.in_(
        select(YtChannel.channel_id).where(
            YtChannel.status == ChannelStatus.SEED
        )
    )


async def known_video_ids(
    session: AsyncSession, video_ids: Iterable[str]
) -> set[str]:
    """Which of these videos are tracked, finished ones included."""
    wanted = list(dict.fromkeys(video_ids))
    if not wanted:
        return set()
    rows = await session.scalars(
        select(YtRawVideo.video_id).where(YtRawVideo.video_id.in_(wanted))
    )
    return set(rows)


async def due_videos(
    session: AsyncSession, *, now: datetime
) -> Sequence[DueVideo]:
    """Every watched video whose moment has come, oldest due first."""
    rows = await session.execute(
        select(
            YtRawVideo.video_id,
            YtRawVideo.payload,
            YtRawVideo.fetched_at,
            YtRawVideo.discovered_at,
            YtRawVideo.discovered_via,
        )
        .where(YtRawVideo.due_at <= now, _watched())
        .order_by(YtRawVideo.due_at, YtRawVideo.video_id)
    )
    return [
        DueVideo(
            video_id=row.video_id,
            payload=row.payload,
            fetched_at=row.fetched_at,
            discovered_at=row.discovered_at,
            discovered_via=row.discovered_via,
        )
        for row in rows
    ]


async def store_videos(
    session: AsyncSession,
    writes: Sequence[VideoWrite],
    *,
    observed_at: datetime,
) -> int:
    """Store one answer's videos and readings. Returns how many readings.

    Payloads replace what was stored; a reading is added beside the
    earlier ones, never in their place. One moment for the whole answer,
    passed in rather than taken per row, as ``db/metrics.py`` does and
    for its reason: the videos were all read by one response.

    Payload first, reading second, one transaction — the foreign key
    refuses the other order.
    """
    if not writes:
        return 0
    statement = insert(YtRawVideo).values(
        [
            {
                "video_id": write.video_id,
                "payload": write.payload,
                "fetched_at": observed_at,
                "discovered_at": observed_at,
                "discovered_via": write.discovered_via,
                "due_at": write.due_at,
            }
            for write in writes
        ]
    )
    await session.execute(
        statement.on_conflict_do_update(
            index_elements=[YtRawVideo.video_id],
            set_={
                "payload": statement.excluded.payload,
                "fetched_at": statement.excluded.fetched_at,
                "due_at": statement.excluded.due_at,
            },
        )
    )

    readings = [
        {
            "video_id": write.video_id,
            "observed_at": observed_at,
            "views": write.reading.views,
            "likes": write.reading.likes,
            "comments": write.reading.comments,
        }
        for write in writes
        if write.reading is not None
    ]
    if not readings:
        return 0
    stored = await session.execute(
        insert(YtRawVideoStat)
        .values(readings)
        .on_conflict_do_nothing()
        .returning(YtRawVideoStat.video_id)
    )
    return len(stored.all())


async def videos_due_for_refresh(
    session: AsyncSession, *, older_than: datetime
) -> list[str]:
    """Finished videos whose readings outlive 30 days and whose payload is stale.

    Only once an acceptance date is recorded; before it there are none,
    since no reading outlives its video's payload. A reading taken on or
    after that date may be kept 36 months, and it goes if its video's
    payload expires — so such videos are fetched again, as channels are.
    """
    accepted_on = yt_policy.DERIVED_METRICS_ACCEPTED_ON
    if accepted_on is None:
        return []
    accepted_at = datetime.combine(accepted_on, time(), tzinfo=UTC)
    kept = (
        select(YtRawVideoStat.video_id)
        .where(YtRawVideoStat.observed_at >= accepted_at)
        .distinct()
    )
    rows = await session.scalars(
        select(YtRawVideo.video_id)
        .where(
            YtRawVideo.due_at.is_(None),
            YtRawVideo.fetched_at < older_than,
            YtRawVideo.video_id.in_(kept),
        )
        .order_by(YtRawVideo.fetched_at, YtRawVideo.video_id)
    )
    return list(rows)


async def replace_payloads(
    session: AsyncSession,
    payloads: dict[str, dict[str, Any]],
    *,
    fetched_at: datetime,
) -> None:
    """Replace stored video payloads and nothing else: no reading, no schedule."""
    for video_id, payload in payloads.items():
        await session.execute(
            update(YtRawVideo)
            .where(YtRawVideo.video_id == video_id)
            .values(payload=payload, fetched_at=fetched_at)
            .execution_options(synchronize_session=False)
        )


async def finish_videos(
    session: AsyncSession, video_ids: Iterable[str]
) -> None:
    """Stop reading these videos; their rows stay until they expire.

    For a video whose last reading was missed and which is now past the
    horizon: reading it late would be a reading the schedule never asked
    for, so it is closed without a request.
    """
    ids = list(dict.fromkeys(video_ids))
    if ids:
        await session.execute(
            update(YtRawVideo)
            .where(YtRawVideo.video_id.in_(ids))
            .values(due_at=None)
            .execution_options(synchronize_session=False)
        )


async def remove_videos(
    session: AsyncSession, video_ids: Iterable[str]
) -> list[str]:
    """Forget videos YouTube no longer returns, readings and all.

    Returns the ids that were tracked. One that was not — a candidate
    seen in a listing and gone by the time it was asked for — had
    nothing to forget.
    """
    ids = list(dict.fromkeys(video_ids))
    if not ids:
        return []
    removed = await session.scalars(
        delete(YtRawVideo)
        .where(YtRawVideo.video_id.in_(ids))
        .returning(YtRawVideo.video_id)
    )
    return list(removed)


async def purge_expired_videos(
    session: AsyncSession, *, now: datetime | None = None
) -> tuple[int, int]:
    """Delete video payloads and readings past their time. Returns both counts.

    A payload goes 30 days after its last fetch, taking its readings
    with it. A reading goes 30 days after it was taken — unless the
    derived-metrics use case has been accepted and the reading was taken
    on or after that date, when it may stay 36 calendar months. Postgres
    counts the months, so a month is a month and not 30 days.

    Readings first: a payload removed second would take them anyway, but
    the count of readings expired on their own clock would be wrong.
    """
    moment = now or datetime.now(UTC)
    cutoff = moment - yt_policy.RETENTION

    old_reading = YtRawVideoStat.observed_at < cutoff
    accepted_on = yt_policy.DERIVED_METRICS_ACCEPTED_ON
    if accepted_on is not None:
        accepted_at = datetime.combine(accepted_on, time(), tzinfo=UTC)
        limit = literal(moment, DateTime(timezone=True)) - func.make_interval(
            0, yt_policy.STATISTICS_RETENTION_MONTHS
        )
        kept_longer = and_(
            YtRawVideoStat.observed_at >= accepted_at,
            YtRawVideoStat.observed_at >= limit,
        )
        old_reading = and_(old_reading, not_(kept_longer))

    readings = await session.execute(
        delete(YtRawVideoStat)
        .where(old_reading)
        .returning(YtRawVideoStat.video_id)
    )
    videos = await session.execute(
        delete(YtRawVideo)
        .where(YtRawVideo.fetched_at < cutoff)
        .returning(YtRawVideo.video_id)
    )
    return len(videos.all()), len(readings.all())


async def count_tracked(session: AsyncSession) -> int:
    """How many videos are still being read or checked."""
    return (
        await session.scalar(
            select(func.count())
            .select_from(YtRawVideo)
            .where(YtRawVideo.due_at.is_not(None), _watched())
        )
        or 0
    )


async def count_due(session: AsyncSession, *, now: datetime) -> int:
    """How many watched videos are due now."""
    return (
        await session.scalar(
            select(func.count())
            .select_from(YtRawVideo)
            .where(YtRawVideo.due_at <= now, _watched())
        )
        or 0
    )


async def oldest_due(
    session: AsyncSession, *, now: datetime
) -> datetime | None:
    """The earliest due moment already passed, or ``None`` if none has."""
    oldest: datetime | None = await session.scalar(
        select(func.min(YtRawVideo.due_at)).where(
            YtRawVideo.due_at <= now, _watched()
        )
    )
    return oldest


async def discoveries_since(
    session: AsyncSession, *, since: datetime
) -> dict[YtDiscoveryPath, int]:
    """How many videos each discovery path found since a moment.

    Read from the videos themselves, so it reaches back only as far as
    their data does — 30 days, which is more than the week the status
    command asks for.
    """
    rows = await session.execute(
        select(YtRawVideo.discovered_via, func.count())
        .where(YtRawVideo.discovered_at >= since)
        .group_by(YtRawVideo.discovered_via)
    )
    found = {path: 0 for path in YtDiscoveryPath}
    for path, count in rows.tuples():
        found[path] = count
    return found
