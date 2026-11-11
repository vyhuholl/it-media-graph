"""The search queue: round-robin in YouTube's order, and `--add`.

Pure ranking first, then `itgraph yt search-candidates` end to end —
offline by construction. Every query and id is invented.
"""

from datetime import UTC, datetime, timedelta

import httpx
import pytest
from sqlalchemy import func, select
from typer.testing import CliRunner
from ytfakes import channel_id
from ytinventory import Step, arrange, channels, records

from itgraph.cli import app
from itgraph.config import settings
from itgraph.db import session as db_session
from itgraph.db.models import (
    ChannelStatus,
    YtDiscoverySource,
    YtRawSearchResult,
    YtSearchQuery,
    YtSearchRun,
    YtSearchType,
)
from itgraph.db.session import Database
from itgraph.db.yt_search import start_run
from itgraph.derive.yt_search import Ranked, Run, RunLine, ranking, report

runner = CliRunner()
A, B, C, D, E = (channel_id(n) for n in range(11, 16))


# --- pure ----------------------------------------------------------------


def test_two_queries_interleaved() -> None:
    runs = [Run("fake one", [A, B, C]), Run("fake two", [D, A, E])]

    ranked = ranking(runs, frozenset())

    assert [row.channel_id for row in ranked] == [A, D, B, C, E]
    # A is credited to the query that reached it first.
    assert ranked[0] == Ranked(A, "fake one")


def test_known_channels_are_skipped() -> None:
    runs = [Run("fake one", [A, B, C])]

    ranked = ranking(runs, frozenset({A, B}))

    assert [row.channel_id for row in ranked] == [C]


def test_a_channel_found_twice_by_one_query_is_taken_once() -> None:
    runs = [Run("fake one", [A, A, B])]

    assert [row.channel_id for row in ranking(runs, frozenset())] == [A, B]


def test_the_report_shows_runs_ids_and_queries_only() -> None:
    lines = report(
        [RunLine("fake one", "video", "2026-11-01 10:00", 3, 2)],
        [Ranked(A, "fake one"), Ranked(B, "fake one")],
        top=1,
    )

    assert lines[0] == "searches in date: 1"
    assert "3 results" in lines[1] and "2 new" in lines[1]
    assert A in lines[3] and "fake one" in lines[3]
    assert lines[-1] == "  … and 1 more"


# --- the command ---------------------------------------------------------


@pytest.fixture(autouse=True)
def offline(database_url: str, monkeypatch: pytest.MonkeyPatch) -> None:
    original = db_session.Database
    monkeypatch.setattr(db_session, "Database", lambda: original(database_url))
    monkeypatch.setattr(settings, "youtube_api_key", None)

    def refuse(*args: object, **kwargs: object) -> None:
        raise AssertionError("yt search-candidates built an HTTP client")

    monkeypatch.setattr(httpx.AsyncClient, "__init__", refuse)


def searched(query: str, found: list[str], *, days_ago: float = 0) -> Step:
    async def step(database: Database) -> None:
        at = datetime.now(UTC) - timedelta(days=days_ago)
        async with database.session() as session:
            await start_run(
                session,
                query,
                YtSearchType.VIDEO,
                list(found),
                asked_at=at,
                published_after=at - timedelta(days=30),
            )

    return step


def _candidates(*args: str) -> list[str]:
    result = runner.invoke(app, ["yt", "search-candidates", *args])
    assert result.exit_code == 0, result.output
    return result.output.splitlines()


def test_a_ranked_row_shows_an_id_and_a_query(database_url: str) -> None:
    arrange(database_url, searched("fake query", [A]))

    lines = _candidates()

    [row] = [line for line in lines if A in line]
    assert "fake query" in row
    assert "Fake Channel" not in "\n".join(lines)


def test_runs_out_of_date_are_not_read(database_url: str) -> None:
    arrange(
        database_url,
        searched("fake old", [A], days_ago=31),
        searched("fake new", [B]),
    )

    lines = _candidates()

    assert not any(A in line for line in lines)
    assert any(B in line for line in lines)
    assert lines[0] == "searches in date: 1"


def test_an_old_run_is_kept_with_its_results(
    database_url: str,
) -> None:
    """Out of date for the report, but nothing removes it."""
    arrange(database_url, searched("fake old", [A], days_ago=31))

    _candidates()

    left: dict[str, int] = {}

    async def count(database: Database) -> None:
        async with database.session() as session:
            for model in (YtSearchRun, YtRawSearchResult, YtSearchQuery):
                left[model.__tablename__] = (
                    await session.scalar(
                        select(func.count()).select_from(model)
                    )
                    or 0
                )

    arrange(database_url, count)
    assert left == {
        "yt_search_runs": 1,
        "yt_raw_search_results": 1,
        "yt_search_queries": 1,
    }


def test_known_channels_are_skipped_and_counted(database_url: str) -> None:
    arrange(
        database_url,
        channels({11: ChannelStatus.SEED, 12: ChannelStatus.REJECTED}),
        searched("fake query", [A, B, C]),
    )

    lines = _candidates()

    assert "3 results,   1 new" in lines[1]
    ranked = [line for line in lines if line.startswith("     ")]
    assert [C in line for line in ranked] == [True]


def test_the_top_hundred_are_added(database_url: str) -> None:
    arrange(
        database_url,
        searched("fake one", [channel_id(n) for n in range(100, 180)]),
        searched("fake two", [channel_id(n) for n in range(200, 280)]),
    )

    _candidates("--add")

    added = [
        channel
        for channel, source in records(database_url).items()
        if source is YtDiscoverySource.SEARCH
    ]
    assert len(added) == 100
    # Taken in turn: fifty from each query.
    assert sum(channel < channel_id(200) for channel in added) == 50


def test_reporting_writes_nothing(database_url: str) -> None:
    arrange(database_url, searched("fake query", [A]))

    _candidates()

    assert records(database_url) == {}
