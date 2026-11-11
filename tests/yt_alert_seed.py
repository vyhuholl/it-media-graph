"""Synthetic YouTube alerts for the tests that deliver, count and route them.

Every value is made up and looks it: channel ids are `UC` and twenty-two
characters of `fake…`, video ids eleven of `fake…`, titles say they are
fakes. A YouTube alert needs its channel's record, its video's payload and
the reading it was scored on, and these write all three, as the poll loop
and the scoring change would.
"""

from datetime import datetime, timedelta

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from itgraph.db.models import (
    ChannelStatus,
    YtAlert,
    YtAlertKind,
    YtChannel,
    YtChannelKind,
    YtDiscoveryPath,
    YtDiscoverySource,
    YtLang,
    YtRawChannel,
    YtRawVideo,
    YtRawVideoStat,
)


def fake_channel(n: int) -> str:
    """A channel id of the shape YouTube's have: UC and 22 characters."""
    return f"UCfakeChannel{n:011d}"


def fake_video(n: int) -> str:
    """A video id of the shape YouTube's have: 11 characters."""
    return f"fakeVid{n:04d}"


async def seed_channel(
    session: AsyncSession,
    n: int,
    *,
    lang: YtLang | None,
    fetched_at: datetime | None,
    status: ChannelStatus = ChannelStatus.SEED,
    title: str | None = None,
) -> str:
    """A channel record, and its payload unless ``fetched_at`` is None."""
    channel_id = fake_channel(n)
    session.add(
        YtChannel(
            channel_id=channel_id,
            status=status,
            kind=YtChannelKind.PERSONAL
            if status is ChannelStatus.SEED
            else None,
            lang=lang,
            discovered_via=YtDiscoverySource.MANUAL,
        )
    )
    await session.flush()
    if fetched_at is not None:
        session.add(
            YtRawChannel(
                channel_id=channel_id,
                payload={
                    "id": channel_id,
                    "snippet": {"title": title or f"A fake channel {n}"},
                },
                fetched_at=fetched_at,
            )
        )
        await session.flush()
    return channel_id


async def seed_alert(
    session: AsyncSession,
    *,
    channel_id: str,
    video: int,
    now: datetime,
    kind: YtAlertKind = YtAlertKind.VIEWS_SPIKE,
    raised_at: datetime | None = None,
    published_ago: timedelta = timedelta(hours=5),
    fetched_ago: timedelta = timedelta(minutes=10),
    readings: tuple[
        tuple[timedelta, int | None, int | None, int | None], ...
    ] = ((timedelta(hours=1), 12400, 830, 95),),
    title: str = "A fake video's title",
    folded_into: int | None = None,
    value: float = 4.2,
    expected: float | None = None,
) -> int:
    """A tracked video, its readings, and an alert scored on the first.

    ``readings`` are (how long ago, views, likes, comments), the first
    being the one the alert was scored on. Returns the alert's id.
    """
    video_id = fake_video(video)
    session.add(
        YtRawVideo(
            video_id=video_id,
            payload={
                "id": video_id,
                "snippet": {
                    "channelId": channel_id,
                    "title": title,
                    "publishedAt": (now - published_ago).isoformat(),
                },
            },
            fetched_at=now - fetched_ago,
            discovered_at=now - published_ago,
            discovered_via=YtDiscoveryPath.COUNT,
        )
    )
    await session.flush()
    for ago, views, likes, comments in readings:
        session.add(
            YtRawVideoStat(
                video_id=video_id,
                observed_at=now - ago,
                views=views,
                likes=likes,
                comments=comments,
            )
        )
    await session.flush()
    alert = YtAlert(
        kind=kind,
        video_id=video_id,
        scored_at=now - readings[0][0],
        band=1,
        value=value,
        expected=expected,
        raised_at=raised_at or now - readings[0][0],
        folded_into=folded_into,
    )
    session.add(alert)
    await session.flush()
    return alert.id


async def delivery_of(
    session: AsyncSession,
) -> dict[str, tuple[str | None, str | None]]:
    """Each alert's (delivery, route), by video id."""
    rows = await session.execute(
        text("SELECT video_id, delivery::text, route::text FROM yt_alerts")
    )
    return {video: (delivery, route) for video, delivery, route in rows}
