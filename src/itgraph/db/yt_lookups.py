"""The lookups: which channel YouTube said a linked name belongs to.

API data — an answer to ``videos.list`` or ``channels.list`` — so it is
kept at most 30 days, never refreshed and never dumped (the ``yt_raw_``
prefix does the last). A name in date is not asked about again; one past
its 30 days is removed by the expiry every YouTube command runs first,
and the next run that needs it asks again.

Keyed by ``(kind, name)`` with the model's ``YtLookupKind`` rather than
anything Telegram's, so change 5 can store the answers for the names
YouTube's own descriptions link in the same place.
"""

from collections.abc import Mapping
from datetime import UTC, datetime

from sqlalchemy import delete, select
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.ext.asyncio import AsyncSession

from itgraph.db.models import YtLookupKind, YtRawLookup
from itgraph.yt_policy import RETENTION

__all__ = [
    "LookupKey",
    "lookups_in_date",
    "purge_expired_lookups",
    "store_lookups",
]

# A video id with its case kept, a normalized handle, a lowercased
# legacy name — each with what it is.
LookupKey = tuple[YtLookupKind, str]


async def lookups_in_date(
    session: AsyncSession, *, now: datetime | None = None
) -> dict[LookupKey, str | None]:
    """Every answer fetched in the last 30 days: a channel id, or ``None``.

    Expiry has normally removed the rest already; the cutoff here keeps a
    caller from leaning on a row whose removal is merely late.
    """
    cutoff = (now or datetime.now(UTC)) - RETENTION
    rows = await session.execute(
        select(
            YtRawLookup.kind, YtRawLookup.value, YtRawLookup.channel_id
        ).where(YtRawLookup.fetched_at >= cutoff)
    )
    return {(kind, value): channel for kind, value, channel in rows.tuples()}


async def store_lookups(
    session: AsyncSession,
    answers: Mapping[LookupKey, str | None],
    *,
    fetched_at: datetime,
) -> None:
    """Store answers, found or not, replacing any older answer for a name.

    Replaced, never added to, as a payload is: an older answer kept
    beside the new one would be data past its time by another name.
    """
    if not answers:
        return
    statement = insert(YtRawLookup).values(
        [
            {
                "kind": kind,
                "value": value,
                "channel_id": channel,
                "fetched_at": fetched_at,
            }
            for (kind, value), channel in answers.items()
        ]
    )
    await session.execute(
        statement.on_conflict_do_update(
            index_elements=[YtRawLookup.kind, YtRawLookup.value],
            set_={
                "channel_id": statement.excluded.channel_id,
                "fetched_at": statement.excluded.fetched_at,
            },
        )
    )


async def purge_expired_lookups(
    session: AsyncSession, *, now: datetime | None = None
) -> int:
    """Delete every answer fetched more than 30 days ago. Returns how many."""
    cutoff = (now or datetime.now(UTC)) - RETENTION
    result = await session.execute(
        delete(YtRawLookup)
        .where(YtRawLookup.fetched_at < cutoff)
        .returning(YtRawLookup.value)
    )
    return len(result.all())
