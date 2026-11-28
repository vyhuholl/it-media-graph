"""The seeds' featured lists: what each lists in its channel sections.

The answer to ``channelSections.list`` trimmed to channel ids, in
``yt_raw_channel_sections``: every fetch appends a reading, nothing is
replaced or removed, and the history stays raw so an appearance can be
read from any two consecutive readings however much later the second
comes. A seed is read again once its newest reading of any age is older
than ``youtube_sections_after_days``; a reading older than
``youtube_answers_fresh_days`` is not evidence, and the seed's list is
counted out of date.

Only seeds' lists are read and asked for. The readings are kept when
their channel stops being a seed — nothing reads them then — and go at
once with the record, which is never deleted anyway.
"""

from collections.abc import Mapping
from datetime import UTC, datetime, timedelta
from typing import Any

from sqlalchemy import insert, select
from sqlalchemy.ext.asyncio import AsyncSession

from itgraph.config import settings
from itgraph.db.models import ChannelStatus, YtChannel, YtRawChannelSections

__all__ = [
    "featured_lists",
    "sections_due",
    "seeds_without_list",
    "store_sections",
]

Payload = dict[str, Any]


def _seeds() -> Any:
    """The channels whose lists are asked for and read: seeds not gone."""
    return (YtChannel.status == ChannelStatus.SEED) & YtChannel.gone_at.is_(
        None
    )


def _cutoff(now: datetime | None) -> datetime:
    """The oldest fetch a reading may have and still be evidence."""
    return (now or datetime.now(UTC)) - timedelta(
        days=settings.youtube_answers_fresh_days
    )


async def store_sections(
    session: AsyncSession,
    channel_id: str,
    payload: Payload,
    *,
    fetched_at: datetime,
) -> None:
    """Append a seed's sections as a reading, beside whatever is there."""
    await session.execute(
        insert(YtRawChannelSections).values(
            channel_id=channel_id, payload=payload, fetched_at=fetched_at
        )
    )


def _newest_reading() -> Any:
    """Each channel's newest reading of any age, with its payload."""
    return (
        select(
            YtRawChannelSections.channel_id,
            YtRawChannelSections.payload,
            YtRawChannelSections.fetched_at,
        )
        .distinct(YtRawChannelSections.channel_id)
        .order_by(
            YtRawChannelSections.channel_id,
            YtRawChannelSections.fetched_at.desc(),
        )
        .subquery()
    )


async def sections_due(
    session: AsyncSession, *, older_than: datetime
) -> list[str]:
    """Seeds whose newest reading is absent or fetched before
    ``older_than``, by id."""
    newest = _newest_reading()
    rows = await session.scalars(
        select(YtChannel.channel_id)
        .outerjoin(newest, newest.c.channel_id == YtChannel.channel_id)
        .where(
            _seeds(),
            newest.c.channel_id.is_(None) | (newest.c.fetched_at < older_than),
        )
        .order_by(YtChannel.channel_id)
    )
    return list(rows)


async def featured_lists(
    session: AsyncSession, *, now: datetime | None = None
) -> Mapping[str, Payload]:
    """Every seed's newest in-date reading, by the seed's id."""
    newest = _newest_reading()
    rows = await session.execute(
        select(newest.c.channel_id, newest.c.payload)
        .join(YtChannel, YtChannel.channel_id == newest.c.channel_id)
        .where(_seeds(), newest.c.fetched_at >= _cutoff(now))
    )
    return {channel_id: payload for channel_id, payload in rows.tuples()}


async def seeds_without_list(
    session: AsyncSession, *, now: datetime | None = None
) -> int:
    """How many seeds not marked gone have no in-date reading."""
    return len(await sections_due(session, older_than=_cutoff(now)))
