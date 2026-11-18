"""The chart readings: one row per region and fetch, never rewritten.

What :mod:`itgraph.yt.chart` gathered — the videos in chart order, ids
only — appended by every read and kept like a statistics reading: an
older reading is history, not a lie, and the day's chart is whichever
reading the day holds. Nothing is removed by age, and what is worked
out from the readings — which channels are rising — is never stored.
"""

from datetime import datetime

from sqlalchemy import insert, select
from sqlalchemy.ext.asyncio import AsyncSession

from itgraph.db.models import YtRawChart

__all__ = ["charts_of_the_day", "store_chart"]


async def store_chart(
    session: AsyncSession,
    region: str,
    payload: dict[str, object],
    *,
    fetched_at: datetime,
) -> None:
    """Append a region's chart as a reading, beside whatever is there."""
    await session.execute(
        insert(YtRawChart).values(
            region=region, payload=payload, fetched_at=fetched_at
        )
    )


async def charts_of_the_day(
    session: AsyncSession,
    regions: tuple[str, ...],
    *,
    day_start: datetime,
) -> dict[str, dict[str, object]]:
    """Each region's newest reading fetched at or after ``day_start``.

    The day's chart and nothing older: a reading fetched yesterday says
    nothing about today's. Regions with no reading that day are absent,
    and the caller says so.
    """
    newest = (
        select(
            YtRawChart.region,
            YtRawChart.payload,
            YtRawChart.fetched_at,
        )
        .where(YtRawChart.fetched_at >= day_start)
        .distinct(YtRawChart.region)
        .order_by(
            YtRawChart.region,
            YtRawChart.fetched_at.desc(),
        )
        .subquery()
    )
    rows = await session.execute(
        select(newest.c.region, newest.c.payload).where(
            newest.c.region.in_(regions)
        )
    )
    return {region: payload for region, payload in rows.tuples()}
