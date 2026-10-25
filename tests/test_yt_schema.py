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
    YtDiscoveryPath,
    YtRawChannel,
    YtRawVideo,
    YtRawVideoStat,
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
