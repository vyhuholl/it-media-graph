"""The poll loop's videos: payloads, readings, and when each is due.

A video's row holds its latest payload and the schedule the loop
derived from it; its readings hang off that row and go with it. Nothing
about a video lives anywhere else, which is what lets one ``DELETE`` —
of a video YouTube no longer returns — be the whole of forgetting it.

Nothing here decides anything. When a video is due is computed in
``yt_schedule`` and written down here; what a payload means is read in
``derive/yt_videos``.
"""

from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass
from datetime import datetime
from typing import Any

from sqlalchemy import (
    ColumnElement,
    DateTime,
    cast,
    delete,
    func,
    select,
    update,
)
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.ext.asyncio import AsyncSession

from itgraph.db.models import (
    ChannelStatus,
    YtChannel,
    YtDiscoveryPath,
    YtRawVideo,
    YtRawVideoStat,
)
from itgraph.derive.yt_videos import Counters

__all__ = [
    "LOOP_PATHS",
    "DueVideo",
    "FoundVideo",
    "VideoWrite",
    "count_due",
    "count_found_by_walks",
    "count_tracked",
    "discoveries_since",
    "due_videos",
    "finish_videos",
    "known_video_ids",
    "oldest_due",
    "past_uploads_due_for_refresh",
    "remove_videos",
    "replace_payloads",
    "store_found_videos",
    "store_videos",
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


# The paths the poll loop itself finds videos by. The history walk's is
# not among them.
LOOP_PATHS = (
    YtDiscoveryPath.FIRST,
    YtDiscoveryPath.COUNT,
    YtDiscoveryPath.CHECK,
)


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


@dataclass(frozen=True, slots=True)
class FoundVideo:
    """A video the history walk found, ready to be stored.

    ``due_at`` is set only for a broadcast yet to start, which the walk
    hands to the poll loop; every other video it stores is past the
    horizon and never due. ``reading`` is ``None`` for a broadcast yet
    to start, which has nothing to count.
    """

    video_id: str
    payload: dict[str, Any]
    due_at: datetime | None
    reading: Counters | None


async def store_found_videos(
    session: AsyncSession,
    found: Sequence[FoundVideo],
    *,
    fetched_at: datetime,
) -> list[str]:
    """Store what a walk found, never touching a video already stored.

    Returns the ids actually stored. A video the poll loop tracks keeps
    its payload, its schedule and how it was found: ``DO NOTHING`` on
    conflict, where the loop's own write replaces. A reading goes in
    only for a video this call stored, in the same transaction.
    """
    if not found:
        return []
    stored = set(
        await session.scalars(
            insert(YtRawVideo)
            .values(
                [
                    {
                        "video_id": video.video_id,
                        "payload": video.payload,
                        "fetched_at": fetched_at,
                        "discovered_at": fetched_at,
                        "discovered_via": YtDiscoveryPath.BACKFILL,
                        "due_at": video.due_at,
                    }
                    for video in found
                ]
            )
            .on_conflict_do_nothing(index_elements=[YtRawVideo.video_id])
            .returning(YtRawVideo.video_id)
        )
    )
    readings = [
        {
            "video_id": video.video_id,
            "observed_at": fetched_at,
            "views": video.reading.views,
            "likes": video.reading.likes,
            "comments": video.reading.comments,
        }
        for video in found
        if video.reading is not None and video.video_id in stored
    ]
    if readings:
        await session.execute(
            insert(YtRawVideoStat).values(readings).on_conflict_do_nothing()
        )
    return [video.video_id for video in found if video.video_id in stored]


async def past_uploads_due_for_refresh(
    session: AsyncSession, *, older_than: datetime, since: datetime
) -> list[str]:
    """Past uploads the refresh keeps: a year of every seed's, kept fresh.

    Every video of a seed that the poll loop is done with — no longer
    due, and not a broadcast still waiting to start, which the loop
    checks itself — published since ``since`` (the depth back from now),
    whose payload is older than the refresh age. The poll loop's finished
    videos and the walk's alike: together they are the year.

    The publication time is read out of the payload here because it is
    not a column: a text-to-``timestamptz`` cast cannot be generated.
    """
    snippet = YtRawVideo.payload["snippet"]
    published = cast(snippet["publishedAt"].astext, DateTime(timezone=True))
    broadcast = snippet["liveBroadcastContent"].astext
    rows = await session.scalars(
        select(YtRawVideo.video_id)
        .where(
            _watched(),
            YtRawVideo.due_at.is_(None),
            YtRawVideo.fetched_at < older_than,
            func.coalesce(broadcast, "none") != "upcoming",
            published >= since,
        )
        .order_by(YtRawVideo.fetched_at, YtRawVideo.video_id)
    )
    return list(rows)


async def replace_payloads(
    session: AsyncSession,
    payloads: dict[str, dict[str, Any]],
    *,
    fetched_at: datetime,
    readings: Mapping[str, Counters] | None = None,
) -> int:
    """Replace stored video payloads; with ``readings``, take them too.

    No schedule changes. A reading is added beside the earlier ones, as
    the poll loop's are, for each video given one. Returns how many
    readings were stored.
    """
    for video_id, payload in payloads.items():
        await session.execute(
            update(YtRawVideo)
            .where(YtRawVideo.video_id == video_id)
            .values(payload=payload, fetched_at=fetched_at)
            .execution_options(synchronize_session=False)
        )
    rows = [
        {
            "video_id": video_id,
            "observed_at": fetched_at,
            "views": counters.views,
            "likes": counters.likes,
            "comments": counters.comments,
        }
        for video_id, counters in (readings or {}).items()
        if video_id in payloads
    ]
    if not rows:
        return 0
    stored = await session.execute(
        insert(YtRawVideoStat)
        .values(rows)
        .on_conflict_do_nothing()
        .returning(YtRawVideoStat.video_id)
    )
    return len(stored.all())


async def finish_videos(
    session: AsyncSession, video_ids: Iterable[str]
) -> None:
    """Stop reading these videos; their rows and readings stay.

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


async def count_found_by_walks(session: AsyncSession) -> int:
    """How many stored videos the history walk found."""
    return (
        await session.scalar(
            select(func.count())
            .select_from(YtRawVideo)
            .where(YtRawVideo.discovered_via == YtDiscoveryPath.BACKFILL)
        )
        or 0
    )


async def discoveries_since(
    session: AsyncSession, *, since: datetime
) -> dict[YtDiscoveryPath, int]:
    """How many videos each of the poll loop's paths found since a moment.

    Read from the videos themselves. The history walk's finds are left
    out: a year of
    uploads counted beside a week of the loop's would say nothing about
    how often the count misses, which is what these numbers are for.
    """
    rows = await session.execute(
        select(YtRawVideo.discovered_via, func.count())
        .where(
            YtRawVideo.discovered_at >= since,
            YtRawVideo.discovered_via.in_(LOOP_PATHS),
        )
        .group_by(YtRawVideo.discovered_via)
    )
    found = {path: 0 for path in LOOP_PATHS}
    for path, count in rows.tuples():
        found[path] = count
    return found
