"""The seeds' featured lists: what each lists in its channel sections.

API data — the answer to ``channelSections.list`` trimmed to channel ids
— so it lives in ``yt_raw_channel_sections`` under the 30-day rule: read
again by the refresh before the refresh age, replaced by every read,
removed at 30 days by the expiry every YouTube command runs first, and
out of every dump by the prefix.

Only seeds' lists are read and asked for. A list is kept when its
channel stops being a seed — nothing reads it then, and the expiry takes
it — and goes at once with its record, which is never deleted anyway.
"""

from collections.abc import Mapping
from datetime import UTC, datetime
from typing import Any

from sqlalchemy import delete, select
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.ext.asyncio import AsyncSession

from itgraph.db.models import ChannelStatus, YtChannel, YtRawChannelSections
from itgraph.yt_policy import RETENTION

__all__ = [
    "featured_lists",
    "purge_expired_sections",
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
    """Every seed's list in date, by the seed's id.

    Expiry has normally removed the rest already; the cutoff here keeps
    a reader from leaning on a row whose removal is merely late.
    """
    cutoff = (now or datetime.now(UTC)) - RETENTION
    rows = await session.execute(
        select(YtRawChannelSections.channel_id, YtRawChannelSections.payload)
        .join(
            YtChannel, YtChannel.channel_id == YtRawChannelSections.channel_id
        )
        .where(_seeds(), YtRawChannelSections.fetched_at >= cutoff)
    )
    return {channel_id: payload for channel_id, payload in rows.tuples()}


async def seeds_without_list(
    session: AsyncSession, *, now: datetime | None = None
) -> int:
    """How many seeds not marked gone have no list in date."""
    cutoff = (now or datetime.now(UTC)) - RETENTION
    rows = await session.scalars(
        select(YtChannel.channel_id)
        .outerjoin(
            YtRawChannelSections,
            (YtRawChannelSections.channel_id == YtChannel.channel_id)
            & (YtRawChannelSections.fetched_at >= cutoff),
        )
        .where(_seeds(), YtRawChannelSections.channel_id.is_(None))
    )
    return len(list(rows))


async def purge_expired_sections(
    session: AsyncSession, *, now: datetime | None = None
) -> int:
    """Delete every list fetched more than 30 days ago. Returns how many."""
    cutoff = (now or datetime.now(UTC)) - RETENTION
    result = await session.execute(
        delete(YtRawChannelSections)
        .where(YtRawChannelSections.fetched_at < cutoff)
        .returning(YtRawChannelSections.channel_id)
    )
    return len(result.all())
