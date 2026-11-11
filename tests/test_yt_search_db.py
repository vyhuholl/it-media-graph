"""Where searches live: the operator's queries, the runs, the results."""

from datetime import UTC, datetime, timedelta

from sqlalchemy import func, select, text
from ytfakes import channel_id

from itgraph.db.models import (
    YtRawSearchResult,
    YtSearchQuery,
    YtSearchRun,
    YtSearchType,
)
from itgraph.db.session import Database
from itgraph.db.yt_search import (
    add_page,
    latest_run_in_date,
    runs_in_date,
    start_run,
)

NOW = datetime.now(UTC)
VIDEO = YtSearchType.VIDEO


async def _start(
    database: Database,
    query: str = "fake query",
    channels: list[str | None] | None = None,
    *,
    at: datetime = NOW,
    kind: YtSearchType = VIDEO,
) -> int:
    async with database.session() as session:
        return await start_run(
            session,
            query,
            kind,
            channels if channels is not None else [channel_id(1)],
            asked_at=at,
            published_after=at - timedelta(days=30),
        )


async def _count(database: Database, model: type) -> int:
    async with database.session() as session:
        return (
            await session.scalar(select(func.count()).select_from(model)) or 0
        )


async def test_a_runs_results_hold_a_channel_id_each(
    database: Database,
) -> None:
    run = await _start(database, channels=[channel_id(1), None, channel_id(2)])
    async with database.session() as session:
        await add_page(session, run, 4, [channel_id(3)])

    async with database.session() as session:
        rows = (
            (
                await session.execute(
                    select(
                        YtRawSearchResult.position,
                        YtRawSearchResult.channel_id,
                    ).order_by(YtRawSearchResult.position)
                )
            )
            .tuples()
            .all()
        )
        stored_run = await session.get(YtSearchRun, run)
    # The unread result keeps its place empty; positions stay YouTube's.
    assert rows == [(1, channel_id(1)), (3, channel_id(2)), (4, channel_id(3))]
    assert stored_run is not None and stored_run.pages == 2


async def test_a_query_asked_twice_is_one_query(database: Database) -> None:
    september = NOW - timedelta(days=60)
    await _start(database, at=september)
    await _start(database, at=NOW)

    async with database.session() as session:
        [query] = await session.scalars(select(YtSearchQuery))
    assert (query.first_asked_at, query.last_asked_at) == (september, NOW)


async def test_another_type_is_another_query(database: Database) -> None:
    await _start(database, kind=YtSearchType.VIDEO)
    await _start(database, kind=YtSearchType.CHANNEL)

    assert await _count(database, YtSearchQuery) == 2


async def test_the_latest_run_in_date(database: Database) -> None:
    await _start(database, at=NOW - timedelta(days=40))
    await _start(database, at=NOW - timedelta(days=10))

    async with database.session() as session:
        latest = await latest_run_in_date(session, "fake query", VIDEO)
        other = await latest_run_in_date(
            session, "fake query", YtSearchType.CHANNEL
        )
    assert latest == NOW - timedelta(days=10)
    assert other is None


async def test_runs_in_date_come_oldest_first_with_their_channels(
    database: Database,
) -> None:
    await _start(database, "fake second", [channel_id(2)], at=NOW)
    await _start(
        database,
        "fake first",
        [channel_id(1), channel_id(3)],
        at=NOW - timedelta(days=1),
    )
    await _start(database, "fake gone", at=NOW - timedelta(days=31))

    async with database.session() as session:
        runs = await runs_in_date(session)

    assert [(run.query, run.channels) for run in runs] == [
        ("fake first", (channel_id(1), channel_id(3))),
        ("fake second", (channel_id(2),)),
    ]


async def test_removing_a_run_removes_its_results(database: Database) -> None:
    await _start(database)

    async with database.session() as session:
        await session.execute(text("DELETE FROM yt_search_runs"))

    assert await _count(database, YtRawSearchResult) == 0


async def test_a_restored_query_is_not_taken_for_a_recent_run(
    database: Database,
) -> None:
    """A dump keeps the query and leaves the runs out."""
    await _start(database, at=NOW - timedelta(days=5))
    async with database.session() as session:
        await session.execute(text("DELETE FROM yt_search_runs"))

    async with database.session() as session:
        assert await latest_run_in_date(session, "fake query", VIDEO) is None
