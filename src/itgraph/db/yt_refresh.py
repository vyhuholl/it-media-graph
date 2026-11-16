"""The refresh's own record: one row per run of `yt refresh`.

The refresh is a daily job, and its last step — the late readings of past
uploads — can stop for quota without failing anything a cluster would
notice. This row is what says whether a run did everything that was due:
written once, at the run's end, so a run killed part way leaves none and
does not count as complete. Counts of its own work and its error, no API
data.
"""

from datetime import datetime

from sqlalchemy.ext.asyncio import AsyncSession

from itgraph.db.models import YtPassOutcome, YtRefreshRun

__all__ = ["record_run"]


async def record_run(
    session: AsyncSession,
    *,
    started_at: datetime,
    finished_at: datetime,
    outcome: YtPassOutcome,
    late_due: int,
    late_read: int,
    error: str | None,
) -> None:
    session.add(
        YtRefreshRun(
            started_at=started_at,
            finished_at=finished_at,
            outcome=outcome,
            late_due=late_due,
            late_read=late_read,
            error=error,
        )
    )
