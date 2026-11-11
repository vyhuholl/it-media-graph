"""Synthetic YouTube channels, videos and readings for the scoring tests.

Every id is invented and says so (`ytfakes`), every count made up. Videos
grow along a shape like the one measured — a quarter of a day's views at
four hours, half at eight, half as much again by three days — so that a fit
finds a curve and a spike stands out of it.
"""

import json
from collections.abc import Sequence
from dataclasses import dataclass
from datetime import datetime, timedelta

from sqlalchemy import text
from ytfakes import channel_id, video_resource

from itgraph.db.session import Database

# The poll loop's reading ages, in hours, through a week.
AGES = (1, 2, 4, 8, 12, 18, 24, 36, 48, 72, 96, 120, 168)

# A fraction of the day's value at each age: views, likes, comments.
SHAPE = {
    "views": {
        1: 0.03,
        2: 0.06,
        4: 0.25,
        8: 0.54,
        12: 0.72,
        18: 0.88,
        24: 1.0,
        36: 1.15,
        48: 1.24,
        72: 1.39,
        96: 1.49,
        120: 1.54,
        168: 1.70,
    },
    "likes": {
        1: 0.17,
        2: 0.30,
        4: 0.46,
        8: 0.67,
        12: 0.80,
        18: 0.92,
        24: 1.0,
        36: 1.08,
        48: 1.16,
        72: 1.25,
        96: 1.33,
        120: 1.37,
        168: 1.48,
    },
    "comments": {
        1: 0.2,
        2: 0.3,
        4: 0.5,
        8: 0.7,
        12: 0.8,
        18: 0.9,
        24: 1.0,
        36: 1.1,
        48: 1.2,
        72: 1.3,
        96: 1.3,
        120: 1.4,
        168: 1.4,
    },
}


# How far each watched video of a channel sits from its level, in turn: the
# newest five have a median of exactly one, so a reference of the channel's
# own is its level exactly, and the spread has residuals to measure.
SPREAD = (1.0, 0.8, 1.25, 0.9, 1.1, 0.95)


async def seed_channel(
    database: Database,
    number: int,
    *,
    kind: str | None = "personal",
    lang: str | None = "en",
    status: str = "seed",
) -> str:
    """A channel record. A seed must carry a kind and a language."""
    channel = channel_id(number)
    async with database.session() as session:
        await session.execute(
            text(
                "INSERT INTO yt_channels (channel_id, status, kind, lang, "
                "discovered_via, reject_reason) VALUES (:id, :status, :kind, "
                ":lang, 'manual', :reason)"
            ),
            {
                "id": channel,
                "status": status,
                "kind": kind,
                "lang": lang,
                "reason": "not_it" if status == "rejected" else None,
            },
        )
    return channel


@dataclass(frozen=True, slots=True)
class Video:
    """One video to seed, and its readings: (moment, views, likes, comments)."""

    number: int
    channel: int
    published: datetime
    duration: str | None = "PT12M30S"
    broadcast: bool = False
    readings: Sequence[
        tuple[datetime, int | None, int | None, int | None]
    ] = ()


async def seed_videos(database: Database, videos: Sequence[Video]) -> None:
    """Store the videos' payloads and their readings."""
    payloads = []
    for video in videos:
        payload = video_resource(
            video.number,
            published=video.published,
            channel=video.channel,
            live_started=video.published if video.broadcast else None,
        )
        if video.duration is None:
            del payload["contentDetails"]
        else:
            payload["contentDetails"] = {"duration": video.duration}
        payloads.append(
            {
                "id": payload["id"],
                "payload": json.dumps(payload),
                "at": video.published,
            }
        )
    readings = [
        {
            "id": payloads[index]["id"],
            "at": moment,
            "views": views,
            "likes": likes,
            "comments": comments,
        }
        for index, video in enumerate(videos)
        for moment, views, likes, comments in video.readings
    ]
    async with database.session() as session:
        if payloads:
            await session.execute(
                text(
                    "INSERT INTO yt_raw_videos (video_id, payload, fetched_at, "
                    "discovered_at, discovered_via) VALUES "
                    "(:id, CAST(:payload AS jsonb), :at, :at, 'count')"
                ),
                payloads,
            )
        if readings:
            await session.execute(
                text(
                    "INSERT INTO yt_raw_video_stats (video_id, observed_at, "
                    "views, likes, comments) VALUES "
                    "(:id, :at, :views, :likes, :comments)"
                ),
                readings,
            )


def grown(
    published: datetime,
    *,
    until: datetime,
    views: float,
    likes: float | None = None,
    comments: float | None = None,
    ages: Sequence[float] = AGES,
    boost: float = 1.0,
) -> list[tuple[datetime, int | None, int | None, int | None]]:
    """Readings of a video along the shape, its day value given, up to
    ``until``. ``boost`` multiplies every signal: a video doing that much
    better than its channel's."""
    readings = []
    for hours in ages:
        moment = published + timedelta(hours=hours)
        if moment > until:
            break
        readings.append(
            (
                moment,
                round(views * boost * SHAPE["views"][int(hours)]),
                None
                if likes is None
                else round(likes * boost * SHAPE["likes"][int(hours)]),
                None
                if comments is None
                else round(comments * boost * SHAPE["comments"][int(hours)]),
            )
        )
    return readings


def watched(
    number: int,
    channel: int,
    *,
    now: datetime,
    days_ago: float,
    views: float = 1000,
    duration: str = "PT12M30S",
    boost: float = 1.0,
    comments: float | None = None,
) -> Video:
    """A video the poll loop read from its first hours, up to now."""
    published = now - timedelta(days=days_ago)
    return Video(
        number=number,
        channel=channel,
        published=published,
        duration=duration,
        readings=grown(
            published,
            until=now,
            views=views,
            likes=views / 20,
            comments=views / 200 if comments is None else comments,
            boost=boost,
        ),
    )


def settled(
    number: int,
    channel: int,
    *,
    now: datetime,
    days_ago: float,
    views: float = 1500,
    read_at_days: tuple[float, ...] = (35,),
    duration: str = "PT12M30S",
) -> Video:
    """A past upload, read by the refresh at these ages."""
    published = now - timedelta(days=days_ago)
    return Video(
        number=number,
        channel=channel,
        published=published,
        duration=duration,
        readings=[
            (
                published + timedelta(days=age),
                round(views * (1 + age / 100)),
                round(views / 20),
                round(views / 200),
            )
            for age in read_at_days
            if published + timedelta(days=age) <= now
        ],
    )


async def a_channel(
    database: Database,
    channel: int,
    *,
    now: datetime,
    kind: str = "personal",
    lang: str = "en",
    watched_videos: int = 6,
    settled_videos: int = 4,
    views: float = 1000,
    duration: str = "PT12M30S",
    comments: Sequence[float] | None = None,
) -> None:
    """A channel with watched videos over the last fortnight and settled ones
    behind them."""
    await seed_channel(database, channel, kind=kind, lang=lang)
    base = channel * 100
    await seed_videos(
        database,
        [
            watched(
                base + index,
                channel,
                now=now,
                days_ago=2 + 2 * index,
                views=views,
                duration=duration,
                boost=SPREAD[index % len(SPREAD)],
                comments=None
                if comments is None
                else comments[index % len(comments)],
            )
            for index in range(watched_videos)
        ]
        + [
            settled(
                base + 50 + index,
                channel,
                now=now,
                days_ago=40 + index,
                views=views * 1.5,
                duration=duration,
            )
            for index in range(settled_videos)
        ],
    )
