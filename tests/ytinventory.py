"""Setting up the YouTube inventory for the review queues' tests.

Records in given statuses, featured lists, and reading back what a
queue's `--add` created. Every id is a fake from `ytfakes`.
"""

import asyncio
from collections.abc import Awaitable, Callable
from datetime import UTC, datetime, timedelta

from sqlalchemy import select
from ytfakes import (
    channel_id,
    channel_resource,
    sections_response,
    video_id,
    video_resource,
)

from itgraph.db.models import (
    ChannelStatus,
    YtChannel,
    YtChannelKind,
    YtDiscoveryPath,
    YtDiscoverySource,
    YtLang,
    YtRejectReason,
)
from itgraph.db.session import Database
from itgraph.db.yt_channels import create_channels, store_payloads
from itgraph.db.yt_sections import store_sections
from itgraph.db.yt_videos import VideoWrite, store_videos

Step = Callable[[Database], Awaitable[None]]


def arrange(database_url: str, *steps: Step) -> None:
    """Run setup coroutines, each given a database, in order."""

    async def run() -> None:
        database = Database(database_url)
        try:
            for step in steps:
                await step(database)
        finally:
            await database.dispose()

    asyncio.run(run())


def channels(
    statuses: dict[int, ChannelStatus], *, payloads: bool = True
) -> Step:
    """Records `channel_id(n)` in these statuses, with payloads if asked."""

    async def step(database: Database) -> None:
        async with database.session() as session:
            await create_channels(session, [channel_id(n) for n in statuses])
            if payloads:
                await store_payloads(
                    session,
                    {channel_id(n): channel_resource(n) for n in statuses},
                    fetched_at=datetime.now(UTC),
                )
            for number, status in statuses.items():
                record = await session.get(YtChannel, channel_id(number))
                assert record is not None
                record.status = status
                if status is not ChannelStatus.CANDIDATE:
                    record.kind = YtChannelKind.PERSONAL
                    record.lang = YtLang.EN
                    record.reviewed_at = datetime.now(UTC)
                if status is ChannelStatus.REJECTED:
                    record.reject_reason = YtRejectReason.NOT_IT

    return step


def featured(
    number: int, *listed: int, fetched_at: datetime | None = None
) -> Step:
    """Seed `channel_id(number)`'s featured list naming these channels."""

    async def step(database: Database) -> None:
        async with database.session() as session:
            await store_sections(
                session,
                channel_id(number),
                sections_response([channel_id(n) for n in listed]),
                fetched_at=fetched_at or datetime.now(UTC),
            )

    return step


def video(number: int, *, channel: int, description: str) -> Step:
    """A stored video `video_id(number)` of `channel_id(channel)`."""

    async def step(database: Database) -> None:
        async with database.session() as session:
            await store_videos(
                session,
                [
                    VideoWrite(
                        video_id=video_id(number),
                        payload=video_resource(
                            number,
                            published=datetime.now(UTC) - timedelta(days=60),
                            channel=channel,
                            description=description,
                        ),
                        due_at=None,
                        reading=None,
                        discovered_via=YtDiscoveryPath.CHECK,
                    )
                ],
                observed_at=datetime.now(UTC),
            )

    return step


def records(database_url: str) -> dict[str, YtDiscoverySource]:
    """Every record's id and discovery source."""
    found: dict[str, YtDiscoverySource] = {}

    async def read(database: Database) -> None:
        async with database.session() as session:
            rows = await session.execute(
                select(YtChannel.channel_id, YtChannel.discovered_via)
            )
            found.update({channel: via for channel, via in rows.tuples()})

    arrange(database_url, read)
    return found
