"""The lookups: which channel YouTube said a linked name belongs to.

An answer to ``videos.list`` or ``channels.list``, found or not. A name
answered within ``youtube_answers_fresh_days`` is not asked about again;
an older answer is not read, and the next run that needs it asks again
and replaces it.

Keyed by ``(kind, name)`` with the model's ``YtLookupKind`` rather than
anything Telegram's, so the names YouTube's own descriptions link are
answered in the same place.
"""

from collections.abc import Mapping
from datetime import UTC, datetime, timedelta

from sqlalchemy import select
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.ext.asyncio import AsyncSession

from itgraph.config import settings
from itgraph.db.models import YtLookupKind, YtRawLookup

__all__ = [
    "LookupKey",
    "lookups_in_date",
    "store_lookups",
]

# A video id with its case kept, a normalized handle, a lowercased
# legacy name — each with what it is.
LookupKey = tuple[YtLookupKind, str]


async def lookups_in_date(
    session: AsyncSession, *, now: datetime | None = None
) -> dict[LookupKey, str | None]:
    """Every answer still current: a channel id, or ``None``."""
    cutoff = (now or datetime.now(UTC)) - timedelta(
        days=settings.youtube_answers_fresh_days
    )
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
    """Store answers, found or not, replacing any older answer for a name."""
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
