"""`itgraph yt mentions` end to end.

Offline by construction: no key, no HTTP client, nothing written. What
it prints is ordered by distinct seeds and bounded by the options; what
it logs names ids and nothing YouTube said.
"""

import asyncio
import json
import logging
from datetime import UTC, datetime, timedelta
from pathlib import Path

import httpx
import pytest
from typer.testing import CliRunner
from ytfakes import channel_id, channel_resource, video_id, video_resource

from itgraph.cli import app
from itgraph.config import settings
from itgraph.db import session as db_session
from itgraph.db.models import (
    ChannelStatus,
    YtChannel,
    YtChannelKind,
    YtDiscoveryPath,
    YtLang,
)
from itgraph.db.session import Database
from itgraph.db.yt_channels import create_channels, store_payloads
from itgraph.db.yt_videos import VideoWrite, store_videos

runner = CliRunner()

NOW = datetime.now(UTC)
TEXTS = json.loads(
    (Path(__file__).parent / "fixtures" / "yt_texts.json").read_text(
        encoding="utf-8"
    )
)


@pytest.fixture(autouse=True)
def offline(database_url: str, monkeypatch: pytest.MonkeyPatch) -> None:
    """The test database, no API key, and no HTTP client to be had."""
    original = db_session.Database
    monkeypatch.setattr(db_session, "Database", lambda: original(database_url))
    monkeypatch.setattr(settings, "youtube_api_key", None)

    def refuse(*args: object, **kwargs: object) -> None:
        raise AssertionError("yt mentions built an HTTP client")

    monkeypatch.setattr(httpx.AsyncClient, "__init__", refuse)


async def _arrange(
    database_url: str,
    seeds: list[int],
    descriptions: dict[int, str],
    *,
    handles: dict[int, str] | None = None,
) -> None:
    """Seeds with payloads; each video `n` belongs to the seed `n // 10`."""
    database = Database(database_url)
    try:
        async with database.session() as session:
            await create_channels(session, [channel_id(n) for n in seeds])
            await store_payloads(
                session,
                {
                    channel_id(n): channel_resource(
                        n, handle=(handles or {}).get(n)
                    )
                    for n in seeds
                },
                fetched_at=NOW,
            )
            for number in seeds:
                record = await session.get(YtChannel, channel_id(number))
                assert record is not None
                record.status = ChannelStatus.SEED
                record.kind = YtChannelKind.PERSONAL
                record.lang = YtLang.EN
            await store_videos(
                session,
                [
                    VideoWrite(
                        video_id=video_id(n),
                        payload=video_resource(
                            n,
                            published=NOW - timedelta(days=40),
                            channel=n // 10,
                            description=text,
                        ),
                        due_at=None,
                        reading=None,
                        discovered_via=YtDiscoveryPath.CHECK,
                    )
                    for n, text in descriptions.items()
                ],
                observed_at=NOW,
            )
    finally:
        await database.dispose()


def _given(
    database_url: str,
    seeds: list[int],
    descriptions: dict[int, str],
    *,
    handles: dict[int, str] | None = None,
) -> None:
    asyncio.run(_arrange(database_url, seeds, descriptions, handles=handles))


def _mentions(*args: str) -> list[str]:
    result = runner.invoke(app, ["yt", "mentions", *args])
    assert result.exit_code == 0, result.output
    return result.output.splitlines()


def test_the_most_named_target_first(database_url: str) -> None:
    _given(
        database_url,
        [1, 2],
        {
            10: "youtube.com/@fake-lone youtube.com/@fake-shared",
            20: "youtube.com/@fake-shared",
        },
    )

    lines = _mentions()

    shared = next(i for i, line in enumerate(lines) if "@fake-shared" in line)
    lone = next(i for i, line in enumerate(lines) if "@fake-lone" in line)
    assert shared < lone
    assert "2 seeds" in lines[shared]
    assert channel_id(1) in lines[shared] and channel_id(2) in lines[shared]
    assert lines[0] == (
        "read 2 videos and 0 featured lists of 2 seeds: 0 references to "
        "0 known channels, 2 pending targets"
    )


def test_known_channels_with_their_handles(database_url: str) -> None:
    _given(
        database_url,
        [1, 2],
        {10: "Guest: @fake.seed.two", 20: ""},
        handles={2: "fake.seed.two"},
    )

    lines = _mentions()

    known = lines[lines.index("known channels, most seeds first:") + 1]
    assert channel_id(2) in known
    assert "@fake.seed.two" in known
    assert known.rstrip().endswith(channel_id(1))


def test_ties_are_ordered_the_same_way_every_time(
    database_url: str,
) -> None:
    _given(
        database_url,
        [1, 2],
        {10: "youtube.com/@fake-b", 20: "youtube.com/@fake-a"},
    )
    assert _mentions() == _mentions()


def test_bounded_by_evidence(database_url: str) -> None:
    _given(
        database_url,
        [1, 2, 3],
        {
            10: "youtube.com/@fake-one youtube.com/@fake-two "
            "youtube.com/@fake-three",
            20: "youtube.com/@fake-two youtube.com/@fake-three",
            30: "youtube.com/@fake-three",
        },
    )

    output = "\n".join(_mentions("--min-seeds", "2"))

    assert "@fake-three" in output and "@fake-two" in output
    assert "@fake-one" not in output


def test_bounded_by_rows(database_url: str) -> None:
    _given(
        database_url,
        [1],
        {10: "youtube.com/@fake-one youtube.com/@fake-two"},
    )

    output = "\n".join(_mentions("--limit", "1"))

    assert "@fake-one" in output and "@fake-two" not in output
    assert "… and 1 more" in output


def test_what_was_not_followed(database_url: str) -> None:
    _given(
        database_url,
        [1],
        {
            10: "youtube.com/c/FakeCustom bit.ly/fake-short "
            "youtube.com/playlist?list=PLfake t.me/fake_tg_channel "
            "@fake_twitter_name"
        },
    )

    output = _mentions()

    assert output[-2] == (
        "not followed: 1 custom URLs, 1 playlist pages, 1 shortened links, "
        "1 Telegram links, 1 mentions outside the inventory"
    )
    assert output[-1] == "ambiguous handles: none"
    assert "fake_tg_channel" not in "\n".join(output)
    # The reader returns a custom URL's name now, for Telegram's posts;
    # out of a description it is API data and stays a number.
    assert "fakecustom" not in "\n".join(output).lower()


def test_an_ambiguous_handle_names_both_channels(
    database_url: str,
) -> None:
    _given(
        database_url,
        [1, 2, 3],
        {10: "youtube.com/@fake-dup"},
        handles={2: "fake-dup", 3: "fake-dup"},
    )

    output = _mentions()

    assert output[-2:] == [
        "ambiguous handles:",
        f"  @fake-dup carried by {channel_id(2)} {channel_id(3)}",
    ]
    assert "0 references" in output[0]


def test_without_a_key_or_a_client_it_still_reads(
    database_url: str,
) -> None:
    # The autouse fixture removed the key and made any HTTP client fail.
    _given(database_url, [1], {10: "youtube.com/@fake-unknown"})
    assert any("@fake-unknown" in line for line in _mentions())


def test_help_opens_no_database(monkeypatch: pytest.MonkeyPatch) -> None:
    def refuse() -> None:
        raise AssertionError("--help opened a database")

    monkeypatch.setattr(db_session, "Database", refuse)
    result = runner.invoke(app, ["yt", "mentions", "--help"])
    assert result.exit_code == 0


@pytest.mark.parametrize("args", [["--min-seeds", "0"], ["--limit", "-1"]])
def test_a_bound_out_of_range_is_refused_before_the_database(
    monkeypatch: pytest.MonkeyPatch, args: list[str]
) -> None:
    def refuse() -> None:
        raise AssertionError("a refused option opened a database")

    monkeypatch.setattr(db_session, "Database", refuse)
    result = runner.invoke(app, ["yt", "mentions", *args])
    assert result.exit_code == 2


def test_logs_name_ids_and_nothing_youtube_said(
    database_url: str, caplog: pytest.LogCaptureFixture
) -> None:
    _given(
        database_url,
        [1, 2],
        {
            10: TEXTS["channel_links"],
            11: TEXTS["video_links"],
            12: TEXTS["traps"],
            20: TEXTS["scripts"],
        },
        handles={2: "fake.seed.two"},
    )

    with caplog.at_level(logging.DEBUG):
        _mentions()

    logged = "\n".join(entry.getMessage() for entry in caplog.records)
    assert video_id(10) in logged
    for said in (
        "Fake Video",
        "youtube.com",
        "youtu.be",
        "t.me",
        "@",
        "fake-seed-one",
        "fake.seed.two",
        "fake_twitter_name",
        "фейк",
        "bit.ly",
    ):
        assert said not in logged, said
