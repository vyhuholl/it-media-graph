"""The featured queue: evidence, ranking, report, and `--add`.

Pure functions first, then `itgraph yt featured-candidates` end to end
— offline by construction, no key and no HTTP client. Every id is
invented.
"""

import logging
from datetime import UTC, datetime, timedelta

import httpx
import pytest
from sqlalchemy import func, select
from typer.testing import CliRunner
from ytfakes import channel_id
from ytinventory import arrange, channels, featured, records

from itgraph.cli import app
from itgraph.config import settings
from itgraph.db import session as db_session
from itgraph.db.models import (
    ChannelStatus,
    YtDiscoverySource,
    YtRawChannelSections,
)
from itgraph.db.session import Database
from itgraph.derive.yt_featured import Ranked, evidence, ranking, report

SEED = ChannelStatus.SEED
runner = CliRunner()


# --- pure ----------------------------------------------------------------


def test_two_seeds_featuring_one_channel() -> None:
    found = evidence(
        {channel_id(1): [channel_id(9)], channel_id(2): [channel_id(9)]}
    )

    assert found == {channel_id(9): frozenset({channel_id(1), channel_id(2)})}


def test_a_list_naming_the_seed_itself_is_no_evidence() -> None:
    assert evidence({channel_id(1): [channel_id(1)]}) == {}


def test_more_seeds_rank_first_then_by_id() -> None:
    featured_by = {
        channel_id(8): frozenset({channel_id(1)}),
        channel_id(7): frozenset({channel_id(1)}),
        channel_id(9): frozenset({channel_id(1), channel_id(2)}),
    }

    ranked = ranking(featured_by, frozenset())

    assert [row.channel_id for row in ranked] == [
        channel_id(9),
        channel_id(7),
        channel_id(8),
    ]


def test_known_channels_are_never_ranked() -> None:
    featured_by = {channel_id(n): frozenset({channel_id(1)}) for n in (5, 6)}

    ranked = ranking(featured_by, frozenset({channel_id(5)}))

    assert [row.channel_id for row in ranked] == [channel_id(6)]


def test_the_minimum_cuts() -> None:
    featured_by = {channel_id(9): frozenset({channel_id(1)})}

    assert ranking(featured_by, frozenset(), min_seeds=2) == []


def test_the_report_shows_ids_and_counts_and_the_rest() -> None:
    ranked = [
        Ranked(channel_id(9), frozenset({channel_id(1), channel_id(2)})),
        Ranked(channel_id(8), frozenset({channel_id(1)})),
        Ranked(channel_id(7), frozenset({channel_id(2)})),
    ]

    lines = report(lists=2, missing=1, ranked=ranked, top=1)

    assert lines[0] == "read 2 featured lists of seeds"
    assert "1 seeds have no featured list in date" in lines[1]
    assert "`itgraph yt refresh`" in lines[1]
    assert channel_id(9) in lines[3] and "2 seeds" in lines[3]
    assert lines[-1] == "  … and 2 more: 2 with 1 seed"


# --- the command -------------------------------------------------------


@pytest.fixture(autouse=True)
def offline(database_url: str, monkeypatch: pytest.MonkeyPatch) -> None:
    """The test database, no API key, and no HTTP client to be had."""
    original = db_session.Database
    monkeypatch.setattr(db_session, "Database", lambda: original(database_url))
    monkeypatch.setattr(settings, "youtube_api_key", None)

    def refuse(*args: object, **kwargs: object) -> None:
        raise AssertionError("yt featured-candidates built an HTTP client")

    monkeypatch.setattr(httpx.AsyncClient, "__init__", refuse)


def _candidates(*args: str) -> list[str]:
    result = runner.invoke(app, ["yt", "featured-candidates", *args])
    assert result.exit_code == 0, result.output
    return result.output.splitlines()


def test_a_ranked_row_shows_no_api_data(database_url: str) -> None:
    arrange(database_url, channels({1: SEED}), featured(1, 9))

    lines = _candidates()

    [row] = [line for line in lines if channel_id(9) in line]
    assert "1 seed" in row and channel_id(1) in row
    assert "fakehandle" not in "\n".join(lines)
    assert "Fake Channel" not in "\n".join(lines)


def test_seeds_without_a_list_are_counted(database_url: str) -> None:
    arrange(database_url, channels({1: SEED, 2: SEED}), featured(1, 9))

    lines = _candidates()

    assert any("1 seeds have no featured list" in line for line in lines)


def test_the_top_hundred_are_added(database_url: str) -> None:
    arrange(
        database_url,
        channels({1: SEED}),
        featured(1, *range(10, 160)),
    )

    _candidates("--add")

    added = {
        channel
        for channel, source in records(database_url).items()
        if source is YtDiscoverySource.FEATURED
    }
    assert len(added) == 100


def test_a_missing_list_refuses_the_cut(database_url: str) -> None:
    arrange(database_url, channels({1: SEED, 2: SEED}), featured(1, 9))

    result = runner.invoke(app, ["yt", "featured-candidates", "--add"])

    assert result.exit_code == 1
    assert "1 seeds have no featured list" in result.output
    assert "`itgraph yt refresh`" in result.output
    assert channel_id(9) not in records(database_url)


def test_a_rejected_channel_is_never_proposed_again(database_url: str) -> None:
    arrange(
        database_url,
        channels({1: SEED, 9: ChannelStatus.REJECTED}),
        featured(1, 9, 8),
    )

    lines = _candidates("--add")

    assert not any(channel_id(9) in line and "seed" in line for line in lines)
    assert records(database_url)[channel_id(8)] is YtDiscoverySource.FEATURED
    assert records(database_url)[channel_id(9)] is YtDiscoverySource.MANUAL


def test_reporting_writes_nothing(database_url: str) -> None:
    arrange(database_url, channels({1: SEED}), featured(1, 9))
    before = records(database_url)

    _candidates()

    assert records(database_url) == before


def test_a_list_no_longer_current_is_kept_and_not_read(
    database_url: str,
) -> None:
    arrange(
        database_url,
        channels({1: SEED}),
        featured(1, 9, fetched_at=datetime.now(UTC) - timedelta(days=31)),
    )

    lines = _candidates()

    assert lines[0] == "read 0 featured lists of seeds"
    left: list[int] = []

    async def count(database: Database) -> None:
        async with database.session() as session:
            left.append(
                await session.scalar(
                    select(func.count()).select_from(YtRawChannelSections)
                )
                or 0
            )

    arrange(database_url, count)
    assert left == [1]


def test_logs_name_ids_only(
    database_url: str, caplog: pytest.LogCaptureFixture
) -> None:
    arrange(database_url, channels({1: SEED}), featured(1, 9))
    caplog.set_level(logging.DEBUG)

    _candidates()

    assert "fakehandle" not in caplog.text
    assert "Fake Channel" not in caplog.text
