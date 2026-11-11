"""The seeds' featured lists: what each lists in its channel sections.

The answer to ``channelSections.list`` trimmed to channel ids, in
``yt_raw_channel_sections``: read again by the refresh once older than
the refresh age, and replaced by every read. A list older than
``youtube_answers_fresh_days`` is not read as evidence.

Only seeds' lists are read and asked for. A list is kept when its
channel stops being a seed — nothing reads it then — and goes at once
with its record, which is never deleted anyway.
"""

from collections.abc import Mapping
from datetime import UTC, datetime, timedelta
from typing import Any

from sqlalchemy import select
from sqlalchemy.dialects.postgresql import insert
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
    """The oldest fetch a list may have and still be read."""
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
    """Store a seed's sections, replacing whatever was there."""
    statement = insert(YtRawChannelSections).values(
        channel_id=channel_id, payload=payload, fetched_at=fetched_at
    )
    await session.execute(
        statement.on_conflict_do_update(
            index_elements=[YtRawChannelSections.channel_id],
            set_={
                "payload": statement.excluded.payload,
                "fetched_at": statement.excluded.fetched_at,
            },
        )
    )


async def sections_due(
    session: AsyncSession, *, older_than: datetime
) -> list[str]:
    """Seeds whose list is absent or fetched before ``older_than``, by id."""
    rows = await session.scalars(
        select(YtChannel.channel_id)
        .outerjoin(
            YtRawChannelSections,
            YtRawChannelSections.channel_id == YtChannel.channel_id,
        )
        .where(
            _seeds(),
            YtRawChannelSections.channel_id.is_(None)
            | (YtRawChannelSections.fetched_at < older_than),
        )
        .order_by(YtChannel.channel_id)
    )
    return list(rows)


async def featured_lists(
    session: AsyncSession, *, now: datetime | None = None
) -> Mapping[str, Payload]:
    """Every seed's current list, by the seed's id."""
    rows = await session.execute(
        select(YtRawChannelSections.channel_id, YtRawChannelSections.payload)
        .join(
            YtChannel, YtChannel.channel_id == YtRawChannelSections.channel_id
        )
        .where(_seeds(), YtRawChannelSections.fetched_at >= _cutoff(now))
    )
    return {channel_id: payload for channel_id, payload in rows.tuples()}


async def seeds_without_list(
    session: AsyncSession, *, now: datetime | None = None
) -> int:
    """How many seeds not marked gone have no current list."""
    rows = await session.scalars(
        select(YtChannel.channel_id)
        .outerjoin(
            YtRawChannelSections,
            (YtRawChannelSections.channel_id == YtChannel.channel_id)
            & (YtRawChannelSections.fetched_at >= _cutoff(now)),
        )
        .where(_seeds(), YtRawChannelSections.channel_id.is_(None))
    )
    return len(list(rows))
