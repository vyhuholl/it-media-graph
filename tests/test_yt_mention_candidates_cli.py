"""`itgraph yt mention-candidates` end to end.

Offline by construction: no key, no HTTP client. Ranked by distinct
seeds after lookups, shows no API data; with `--add` the top becomes
candidates from `mention`, refused while anything awaits a lookup.
Every id and handle is invented.
"""

import logging
from datetime import UTC, datetime, timedelta

import httpx
import pytest
from typer.testing import CliRunner
from ytfakes import channel_id, video_id
from ytinventory import Step, arrange, channels, records, video

from itgraph.cli import app
from itgraph.config import settings
from itgraph.db import session as db_session
from itgraph.db.models import ChannelStatus, YtDiscoverySource, YtLookupKind
from itgraph.db.session import Database
from itgraph.db.yt_lookups import store_lookups

SEED = ChannelStatus.SEED
runner = CliRunner()


@pytest.fixture(autouse=True)
def offline(database_url: str, monkeypatch: pytest.MonkeyPatch) -> None:
    """The test database, no API key, and no HTTP client to be had."""
    original = db_session.Database
    monkeypatch.setattr(db_session, "Database", lambda: original(database_url))
    monkeypatch.setattr(settings, "youtube_api_key", None)

    def refuse(*args: object, **kwargs: object) -> None:
        raise AssertionError("yt mention-candidates built an HTTP client")

    monkeypatch.setattr(httpx.AsyncClient, "__init__", refuse)


def looked_up(
    kind: YtLookupKind,
    value: str,
    channel: str | None,
    *,
    at: datetime | None = None,
) -> Step:
    async def step(database: Database) -> None:
        async with database.session() as session:
            await store_lookups(
                session,
                {(kind, value): channel},
                fetched_at=at or datetime.now(UTC),
            )

    return step


def _candidates(*args: str) -> list[str]:
    result = runner.invoke(app, ["yt", "mention-candidates", *args])
    assert result.exit_code == 0, result.output
    return result.output.splitlines()


def _two_seeds_linking(number: int) -> list[Step]:
    return [
        video(
            10 * number + 1,
            channel=1,
            description=f"youtube.com/channel/{channel_id(number)}",
        ),
        video(
            10 * number + 2,
            channel=2,
            description=f"youtube.com/channel/{channel_id(number)}",
        ),
    ]


def test_a_ranked_row_shows_no_api_data(database_url: str) -> None:
    arrange(
        database_url,
        channels({1: SEED, 2: SEED}),
        video(1, channel=1, description="youtube.com/@fake-unknown"),
        video(2, channel=2, description=f"youtu.be/{video_id(50)}"),
        looked_up(YtLookupKind.HANDLE, "fake-unknown", channel_id(8)),
        looked_up(YtLookupKind.VIDEO, video_id(50), channel_id(8)),
    )

    lines = _candidates()

    [row] = [line for line in lines if channel_id(8) in line]
    assert "2 seeds" in row
    assert channel_id(1) in row and channel_id(2) in row
    assert "fake-unknown" not in "\n".join(lines)
    assert "fakehandle" not in "\n".join(lines)


def test_the_top_and_the_rest(database_url: str) -> None:
    steps: list[Step] = [channels({1: SEED, 2: SEED})]
    for number in range(10, 25):
        steps += _two_seeds_linking(number)
    arrange(database_url, *steps)

    lines = _candidates("--top", "10")

    assert sum(1 for line in lines if "2 seeds" in line and "UC" in line) == 10
    assert any("… and 5 more: 5 with 2 seeds" in line for line in lines)


def test_one_seed_is_enough_when_asked(database_url: str) -> None:
    arrange(
        database_url,
        channels({1: SEED}),
        video(
            1, channel=1, description=f"youtube.com/channel/{channel_id(9)}"
        ),
    )

    assert not any(channel_id(9) in line for line in _candidates())
    assert any(
        channel_id(9) in line for line in _candidates("--min-seeds", "1")
    )


def test_what_could_not_be_counted(database_url: str) -> None:
    arrange(
        database_url,
        channels({1: SEED}),
        video(
            1,
            channel=1,
            description=f"youtube.com/@fake-unknown youtu.be/{video_id(50)}",
        ),
        looked_up(YtLookupKind.VIDEO, video_id(50), None),
    )

    lines = _candidates()

    assert (
        "awaiting a lookup: 1 references — run `itgraph yt mention-lookup`"
        in lines
    )
    assert "looked up, no channel: 1 references" in lines


def test_the_top_hundred_are_added(database_url: str) -> None:
    steps: list[Step] = [channels({1: SEED, 2: SEED})]
    for number in range(10, 160):
        steps += _two_seeds_linking(number)
    arrange(database_url, *steps)

    _candidates("--add")

    added = [
        channel
        for channel, source in records(database_url).items()
        if source is YtDiscoverySource.MENTION
    ]
    assert len(added) == 100


def test_an_incomplete_lookup_refuses_the_cut(database_url: str) -> None:
    arrange(
        database_url,
        channels({1: SEED, 2: SEED}),
        *_two_seeds_linking(9),
        video(1, channel=1, description="youtube.com/@fake-unknown"),
    )

    result = runner.invoke(app, ["yt", "mention-candidates", "--add"])

    assert result.exit_code == 1
    assert "1 references still await a lookup" in result.output
    assert "`itgraph yt mention-lookup`" in result.output
    assert channel_id(9) not in records(database_url)


def test_the_next_run_brings_the_next_channels(database_url: str) -> None:
    arrange(
        database_url,
        channels({1: SEED, 2: SEED}),
        *_two_seeds_linking(9),
    )

    _candidates("--add")
    lines = _candidates("--add")

    assert "added 0 candidates" in lines
    assert records(database_url)[channel_id(9)] is YtDiscoverySource.MENTION


def test_reporting_writes_nothing(database_url: str) -> None:
    arrange(
        database_url,
        channels({1: SEED, 2: SEED}),
        *_two_seeds_linking(9),
    )
    before = records(database_url)

    _candidates()

    assert records(database_url) == before


def test_an_expired_lookup_changes_the_next_report(database_url: str) -> None:
    arrange(
        database_url,
        channels({1: SEED, 2: SEED}),
        video(1, channel=1, description=f"youtu.be/{video_id(50)}"),
        video(
            2, channel=2, description=f"youtube.com/channel/{channel_id(8)}"
        ),
        looked_up(
            YtLookupKind.VIDEO,
            video_id(50),
            channel_id(8),
            at=datetime.now(UTC) - timedelta(days=31),
        ),
    )

    lines = _candidates("--min-seeds", "1")

    [row] = [line for line in lines if channel_id(8) in line]
    assert "1 seed " in row
    assert any("awaiting a lookup: 1 references" in line for line in lines)


def test_logs_name_ids_only(
    database_url: str, caplog: pytest.LogCaptureFixture
) -> None:
    arrange(
        database_url,
        channels({1: SEED}),
        video(1, channel=1, description="youtube.com/@fake-unknown"),
    )
    caplog.set_level(logging.DEBUG)

    _candidates()

    assert "fake-unknown" not in caplog.text
    assert "youtube.com/" not in caplog.text
