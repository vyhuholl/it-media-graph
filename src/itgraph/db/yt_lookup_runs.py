"""The scheduled lookup pass's own record: one row per run of `yt
rising-lookups`.

The pass is a daily job whose work is quiet by design — an empty queue
is its healthiest state — so this row is what says it ran at all, and
an empty run is recorded like any other: that is what keeps "nothing
to look up" and "could not run" distinguishable from the outside.
Written once, at the run's end, so a process killed part way leaves no
row. Counts of its own work and its error, no API data.
"""

from datetime import datetime

from sqlalchemy.ext.asyncio import AsyncSession

from itgraph.db.models import YtLookupRun, YtPassOutcome

__all__ = ["record_run"]


async def record_run(
    session: AsyncSession,
    *,
    started_at: datetime,
    finished_at: datetime,
    outcome: YtPassOutcome,
    tg_requests: int,
    tg_awaiting: int,
    mention_requests: int,
    mention_awaiting: int,
    error: str | None,
) -> None:
    session.add(
        YtLookupRun(
            started_at=started_at,
            finished_at=finished_at,
            outcome=outcome,
            tg_requests=tg_requests,
            tg_awaiting=tg_awaiting,
            mention_requests=mention_requests,
            mention_awaiting=mention_awaiting,
            error=error,
        )
    )
