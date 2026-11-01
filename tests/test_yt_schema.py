"""The poll loop's tables hold what the database itself can guarantee.

What is enforced here rather than in Python: a video's channel is read
off its payload by Postgres, so the two cannot disagree; a reading goes
with its video; an id of the wrong shape is refused. And the trim the
migration runs over change 1's payloads leaves exactly the declared
fields — tested by running the migration's own statement.
"""

import importlib.util
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest
from sqlalchemy import func, select, text
from sqlalchemy.exc import IntegrityError
from ytfakes import channel_id, channel_resource, video_id, video_resource

from itgraph.db.models import (
    YtBackfillWalk,
    YtChannel,
    YtCommand,
    YtDiscoveryPath,
    YtDiscoverySource,
    YtLookupKind,
    YtQuotaBucket,
    YtQuotaExhausted,
    YtRawChannel,
    YtRawChannelSections,
    YtRawLookup,
    YtRawSearchResult,
    YtRawVideo,
    YtRawVideoStat,
    YtSearchQuery,
    YtSearchRun,
    YtSearchType,
)
from itgraph.db.session import Database
from itgraph.db.yt_channels import create_channels, store_payloads

NOW = datetime(2026, 10, 26, 12, 0, tzinfo=UTC)
MIGRATION = (
    Path(__file__).parent.parent
    / "src/itgraph/db/migrations/versions/321e40440221_youtube_snapshots.py"
)


def _trim_statement() -> str:
    """The migration's own trim, loaded from the revision file itself."""
    spec = importlib.util.spec_from_file_location(
        "youtube_snapshots", MIGRATION
    )
    assert spec is not None and spec.loader is not None
    migration = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(migration)
    statement: str = migration.TRIM_CHANNEL_PAYLOADS
    return statement


def _video(number: int, *, channel: int = 1) -> YtRawVideo:
    return YtRawVideo(
        video_id=video_id(number),
        payload=video_resource(number, published=NOW, channel=channel),
        fetched_at=NOW,
        discovered_at=NOW,
        discovered_via=YtDiscoveryPath.FIRST,
        due_at=NOW + timedelta(hours=1),
    )


async def test_the_channel_column_follows_the_payload(
    database: Database,
) -> None:
    async with database.session() as session:
        session.add(_video(1, channel=1))

    async with database.session() as session:
        video = await session.get(YtRawVideo, video_id(1))
        assert video is not None and video.channel_id == channel_id(1)
        video.payload = video_resource(1, published=NOW, channel=2)

    async with database.session() as session:
        moved = await session.scalar(
            select(YtRawVideo.channel_id).where(
                YtRawVideo.video_id == video_id(1)
            )
        )
    assert moved == channel_id(2)


async def test_a_reading_goes_with_its_video(database: Database) -> None:
    async with database.session() as session:
        session.add(_video(1))
        await session.flush()
        session.add(YtRawVideoStat(video_id=video_id(1), observed_at=NOW))

    async with database.session() as session:
        await session.execute(
            text("DELETE FROM yt_raw_videos WHERE video_id = :id"),
            {"id": video_id(1)},
        )
        left = await session.scalar(
            select(func.count()).select_from(YtRawVideoStat)
        )
    assert left == 0


async def test_a_walk_goes_with_its_channel(database: Database) -> None:
    """A walk record is about a channel; without the channel it is nothing."""
    async with database.session() as session:
        await create_channels(session, [channel_id(1)])
        session.add(
            YtBackfillWalk(
                channel_id=channel_id(1),
                cutoff=NOW - timedelta(days=365),
                started_at=NOW,
            )
        )

    async with database.session() as session:
        await session.execute(
            text("DELETE FROM yt_channels WHERE channel_id = :id"),
            {"id": channel_id(1)},
        )
        left = await session.scalar(
            select(func.count()).select_from(YtBackfillWalk)
        )
    assert left == 0


async def test_a_malformed_video_id_is_refused(database: Database) -> None:
    video = _video(1)
    video.video_id = "not-a-video-id"

    with pytest.raises(IntegrityError, match="id_shape"):
        async with database.session() as session:
            session.add(video)


async def test_the_trim_leaves_exactly_the_declared_fields(
    database: Database,
) -> None:
    """Change 1's shape in, the shape the declared fields give out."""
    old = channel_resource(1, language="en", country="US")
    old["snippet"]["thumbnails"] = {"default": {"url": "https://fake.invalid"}}
    old["snippet"]["localized"] = {"title": "Fake Channel 1"}
    old["brandingSettings"] = {"channel": {"keywords": "fake keywords"}}
    old["status"] = {"privacyStatus": "public", "madeForKids": False}
    old["topicDetails"] = {"topicCategories": ["https://fake.invalid/topic"]}
    async with database.session() as session:
        await create_channels(session, [channel_id(1)])
        await store_payloads(session, {channel_id(1): old}, fetched_at=NOW)

    async with database.session() as session:
        await session.execute(text(_trim_statement()))

    async with database.session() as session:
        stored = await session.get(YtRawChannel, channel_id(1))
    assert stored is not None
    payload = stored.payload
    assert set(payload) == {"id", "snippet", "contentDetails", "statistics"}
    assert set(payload["snippet"]) == {
        "title",
        "description",
        "customUrl",
        "country",
        "defaultLanguage",
    }
    uploads = old["contentDetails"]["relatedPlaylists"]["uploads"]
    assert payload["contentDetails"] == {
        "relatedPlaylists": {"uploads": uploads}
    }
    assert set(payload["statistics"]) == {
        "viewCount",
        "subscriberCount",
        "hiddenSubscriberCount",
        "videoCount",
    }
    assert payload["statistics"]["hiddenSubscriberCount"] is False


async def test_the_trim_adds_nothing_a_channel_never_had(
    database: Database,
) -> None:
    """No declared language stays no declared language, not a null."""
    async with database.session() as session:
        await create_channels(session, [channel_id(1)])
        await store_payloads(
            session, {channel_id(1): channel_resource(1)}, fetched_at=NOW
        )
        await session.execute(text(_trim_statement()))

    async with database.session() as session:
        stored = await session.get(YtRawChannel, channel_id(1))
    assert stored is not None
    assert "defaultLanguage" not in stored.payload["snippet"]
    assert "country" not in stored.payload["snippet"]


async def test_a_record_from_telegram_is_accepted(database: Database) -> None:
    """The ranking of Telegram links has a discovery source of its own."""
    async with database.session() as session:
        await create_channels(
            session, [channel_id(1)], discovered_via=YtDiscoverySource.TELEGRAM
        )

    async with database.session() as session:
        record = await session.get(YtChannel, channel_id(1))
    assert record is not None
    assert record.discovered_via is YtDiscoverySource.TELEGRAM


@pytest.mark.parametrize(
    ("kind", "value", "channel", "constraint"),
    [
        (
            YtLookupKind.HANDLE,
            "fake-unknown",
            "not-a-channel-id",
            "channel_shape",
        ),
        (YtLookupKind.VIDEO, "not-a-video-id", None, "video_shape"),
    ],
)
async def test_a_malformed_lookup_is_refused(
    database: Database,
    kind: YtLookupKind,
    value: str,
    channel: str | None,
    constraint: str,
) -> None:
    with pytest.raises(IntegrityError, match=constraint):
        async with database.session() as session:
            session.add(
                YtRawLookup(
                    kind=kind, value=value, channel_id=channel, fetched_at=NOW
                )
            )


async def test_a_lookup_may_name_no_channel(database: Database) -> None:
    """Not found is an answer worth keeping; a handle is any name."""
    async with database.session() as session:
        session.add(
            YtRawLookup(
                kind=YtLookupKind.HANDLE,
                value="fake.unknown-two",
                channel_id=None,
                fetched_at=NOW,
            )
        )
        session.add(
            YtRawLookup(
                kind=YtLookupKind.VIDEO,
                value=video_id(1),
                channel_id=channel_id(1),
                fetched_at=NOW,
            )
        )

    async with database.session() as session:
        stored = await session.scalar(
            select(func.count()).select_from(YtRawLookup)
        )
    assert stored == 2


@pytest.mark.parametrize(
    "source",
    [
        YtDiscoverySource.MENTION,
        YtDiscoverySource.FEATURED,
        YtDiscoverySource.SEARCH,
    ],
)
async def test_records_from_youtube_queues_are_accepted(
    database: Database, source: YtDiscoverySource
) -> None:
    async with database.session() as session:
        await create_channels(session, [channel_id(1)], discovered_via=source)

    async with database.session() as session:
        record = await session.get(YtChannel, channel_id(1))
    assert record is not None
    assert record.discovered_via is source


async def test_featured_lists_belong_to_records(database: Database) -> None:
    """A list is a seed's, so its owner must be in the inventory."""
    with pytest.raises(IntegrityError, match="fk_yt_raw_channel_sections"):
        async with database.session() as session:
            session.add(
                YtRawChannelSections(
                    channel_id=channel_id(9), payload={}, fetched_at=NOW
                )
            )


async def test_a_featured_list_goes_with_its_channel(
    database: Database,
) -> None:
    async with database.session() as session:
        await create_channels(
            session, [channel_id(1)], discovered_via=YtDiscoverySource.MANUAL
        )
        session.add(
            YtRawChannelSections(
                channel_id=channel_id(1), payload={"items": []}, fetched_at=NOW
            )
        )

    async with database.session() as session:
        await session.execute(text("DELETE FROM yt_channels"))
    async with database.session() as session:
        left = await session.scalar(
            select(func.count()).select_from(YtRawChannelSections)
        )
    assert left == 0


async def _run(database: Database) -> int:
    async with database.session() as session:
        query = YtSearchQuery(
            query="fake query",
            type=YtSearchType.VIDEO,
            first_asked_at=NOW,
            last_asked_at=NOW,
        )
        session.add(query)
        await session.flush()
        run = YtSearchRun(query_id=query.id, asked_at=NOW, pages=1)
        session.add(run)
        await session.flush()
        return run.id


@pytest.mark.parametrize(
    ("position", "channel", "constraint"),
    [
        (1, "not-a-channel-id", "channel_shape"),
        (0, channel_id(1), "position_from_one"),
    ],
)
async def test_a_malformed_search_result_is_refused(
    database: Database, position: int, channel: str, constraint: str
) -> None:
    run_id = await _run(database)
    with pytest.raises(IntegrityError, match=constraint):
        async with database.session() as session:
            session.add(
                YtRawSearchResult(
                    run_id=run_id, position=position, channel_id=channel
                )
            )


async def test_results_go_with_their_run(database: Database) -> None:
    run_id = await _run(database)
    async with database.session() as session:
        session.add(
            YtRawSearchResult(
                run_id=run_id, position=1, channel_id=channel_id(1)
            )
        )

    async with database.session() as session:
        await session.execute(text("DELETE FROM yt_search_runs"))
    async with database.session() as session:
        left = await session.scalar(
            select(func.count()).select_from(YtRawSearchResult)
        )
    assert left == 0


async def test_a_query_is_one_row_per_type(database: Database) -> None:
    async with database.session() as session:
        for kind in YtSearchType:
            session.add(
                YtSearchQuery(
                    query="fake query",
                    type=kind,
                    first_asked_at=NOW,
                    last_asked_at=NOW,
                )
            )
    with pytest.raises(IntegrityError, match="uq_yt_search_queries"):
        async with database.session() as session:
            session.add(
                YtSearchQuery(
                    query="fake query",
                    type=YtSearchType.VIDEO,
                    first_asked_at=NOW,
                    last_asked_at=NOW,
                )
            )


async def test_one_day_may_be_exhausted_in_both_buckets(
    database: Database,
) -> None:
    """The search bucket's day ends apart from the main bucket's."""
    day = NOW.date()
    async with database.session() as session:
        session.add(
            YtQuotaExhausted(
                day=day, bucket=YtQuotaBucket.MAIN, command=YtCommand.REFRESH
            )
        )
        session.add(
            YtQuotaExhausted(
                day=day, bucket=YtQuotaBucket.SEARCH, command=YtCommand.SEARCH
            )
        )

    async with database.session() as session:
        rows = await session.scalar(
            select(func.count()).select_from(YtQuotaExhausted)
        )
    assert rows == 2
