"""Searches: the operator's queries, each run of one, and what it returned.

Three tables:

- ``yt_search_queries`` — the words the operator typed, with its type
  and when it was first and last asked. The operator's own, and in the
  daily inventory dump.
- ``yt_search_runs`` — one search of one query: when, bounded how, how
  many pages.
- ``yt_raw_search_results`` — each result's channel id and position.

A run is "in date" while it is younger than
``youtube_answers_fresh_days``: a query is not asked again while a run
of it is in date, and only runs in date feed the search queue. Whether
a query may be asked again is read from the runs, never from
``last_asked_at``.
"""

from collections.abc import Sequence
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta

from sqlalchemy import func, select, update
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.ext.asyncio import AsyncSession

from itgraph.config import settings
from itgraph.db.models import (
    YtRawSearchResult,
    YtSearchQuery,
    YtSearchRun,
    YtSearchType,
)

__all__ = [
    "RunInDate",
    "add_page",
    "first_queries",
    "latest_run_in_date",
    "runs_in_date",
    "start_run",
]


def _cutoff(now: datetime | None) -> datetime:
    """The oldest run still in date."""
    return (now or datetime.now(UTC)) - timedelta(
        days=settings.youtube_answers_fresh_days
    )


async def latest_run_in_date(
    session: AsyncSession,
    query: str,
    kind: YtSearchType,
    *,
    now: datetime | None = None,
) -> datetime | None:
    """When the query was last searched for this type, if that is in date."""
    cutoff = _cutoff(now)
    latest: datetime | None = await session.scalar(
        select(func.max(YtSearchRun.asked_at))
        .join(YtSearchQuery, YtSearchQuery.id == YtSearchRun.query_id)
        .where(
            YtSearchQuery.query == query,
            YtSearchQuery.type == kind,
            YtSearchRun.asked_at >= cutoff,
        )
    )
    return latest


def _results(
    run_id: int, first: int, channels: Sequence[str | None]
) -> list[dict[str, object]]:
    """Rows for a page, positions counted on from ``first``; unread ones
    keep their position empty rather than shifting the rest."""
    return [
        {"run_id": run_id, "position": first + offset, "channel_id": channel}
        for offset, channel in enumerate(channels)
        if channel is not None
    ]


async def start_run(
    session: AsyncSession,
    query: str,
    kind: YtSearchType,
    channels: Sequence[str | None],
    *,
    asked_at: datetime,
    published_after: datetime | None,
) -> int:
    """Record a run with its first page, and the query with it.

    Written only once the first page has come back, so a request that
    failed leaves no run to make the query look answered.
    """
    statement = insert(YtSearchQuery).values(
        query=query, type=kind, first_asked_at=asked_at, last_asked_at=asked_at
    )
    query_id = await session.scalar(
        statement.on_conflict_do_update(
            index_elements=[YtSearchQuery.query, YtSearchQuery.type],
            set_={"last_asked_at": statement.excluded.last_asked_at},
        ).returning(YtSearchQuery.id)
    )
    run_id = await session.scalar(
        insert(YtSearchRun)
        .values(
            query_id=query_id,
            asked_at=asked_at,
            published_after=published_after,
            pages=1,
        )
        .returning(YtSearchRun.id)
    )
    assert run_id is not None
    if rows := _results(run_id, 1, channels):
        await session.execute(insert(YtRawSearchResult).values(rows))
    return run_id


async def add_page(
    session: AsyncSession,
    run_id: int,
    first: int,
    channels: Sequence[str | None],
) -> None:
    """Append one more page of a run, its positions counted from ``first``."""
    if rows := _results(run_id, first, channels):
        await session.execute(insert(YtRawSearchResult).values(rows))
    await session.execute(
        update(YtSearchRun)
        .where(YtSearchRun.id == run_id)
        .values(pages=YtSearchRun.pages + 1)
        .execution_options(synchronize_session=False)
    )


@dataclass(frozen=True, slots=True)
class RunInDate:
    """One run in date, with its results' channels in YouTube's order."""

    run_id: int
    query: str
    kind: YtSearchType
    asked_at: datetime
    results: int
    channels: tuple[str, ...]


async def runs_in_date(
    session: AsyncSession, *, now: datetime | None = None
) -> list[RunInDate]:
    """Every run in date, oldest first, each with its channels in order."""
    cutoff = _cutoff(now)
    runs = await session.execute(
        select(
            YtSearchRun.id,
            YtSearchQuery.query,
            YtSearchQuery.type,
            YtSearchRun.asked_at,
        )
        .join(YtSearchQuery, YtSearchQuery.id == YtSearchRun.query_id)
        .where(YtSearchRun.asked_at >= cutoff)
        .order_by(YtSearchRun.asked_at, YtSearchRun.id)
    )
    listed = list(runs.tuples())
    if not listed:
        return []
    results = await session.execute(
        select(YtRawSearchResult.run_id, YtRawSearchResult.channel_id)
        .where(YtRawSearchResult.run_id.in_([run for run, *_ in listed]))
        .order_by(YtRawSearchResult.run_id, YtRawSearchResult.position)
    )
    channels: dict[int, list[str]] = {}
    for run, channel in results.tuples():
        channels.setdefault(run, []).append(channel)
    return [
        RunInDate(
            run_id=run,
            query=query,
            kind=kind,
            asked_at=asked_at,
            results=len(channels.get(run, [])),
            channels=tuple(channels.get(run, [])),
        )
        for run, query, kind, asked_at in listed
    ]


async def first_queries(
    session: AsyncSession, *, now: datetime | None = None
) -> dict[str, str]:
    """Each channel in results in date, and the query of the earliest run
    that returned it — what the review file shows as its source."""
    found: dict[str, str] = {}
    for run in await runs_in_date(session, now=now):
        for channel in run.channels:
            found.setdefault(channel, run.query)
    return found
