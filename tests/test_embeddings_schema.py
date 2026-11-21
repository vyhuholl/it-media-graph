"""What the embedding tables' schema itself guarantees.

The foreign keys are the retention rule: a raw row that goes takes its
embedding with it, on both platforms, and no embedding may name a raw
row that does not exist — the write order is payload first, vector
second. The primary keys keep one vector per text, so a re-embed
replaces and never accumulates: there is no second row for a second
model, the row itself is rewritten under the new ``model_key``.
"""

from datetime import UTC, datetime

import pytest
from sqlalchemy import delete, func, select
from sqlalchemy.exc import IntegrityError
from ytfakes import video_resource

from itgraph.db.models import (
    Channel,
    ChannelStatus,
    DiscoverySource,
    RawMessage,
    TgPostEmbedding,
    YtDiscoveryPath,
    YtRawVideo,
    YtVideoEmbedding,
)
from itgraph.db.session import Database

NOW = datetime(2026, 11, 21, 12, 0, tzinfo=UTC)


async def _count(database: Database, model: type) -> int:
    async with database.session() as session:
        return (
            await session.execute(select(func.count()).select_from(model))
        ).scalar_one()


def _video(number: int) -> YtRawVideo:
    return YtRawVideo(
        video_id=video_resource(number, published=NOW)["id"],
        payload=video_resource(number, published=NOW),
        fetched_at=NOW,
        discovered_at=NOW,
        discovered_via=YtDiscoveryPath.FIRST,
        due_at=None,
    )


async def _seed(database: Database) -> tuple[YtRawVideo, TgPostEmbedding]:
    """A message and a video, each with their embedding in place."""
    video = _video(1)
    message_embedding = TgPostEmbedding(
        channel_id=3,
        msg_id=10,
        model_key="multilingual-e5-small",
        vec=b"\x00\x01",
    )
    video_embedding = YtVideoEmbedding(
        video_id=video.video_id,
        model_key="multilingual-e5-small",
        content_hash="abc123",
        vec=b"\x00\x01",
    )
    async with database.session() as session:
        session.add(
            Channel(
                tg_id=3,
                username="fake_channel_3",
                status=ChannelStatus.SEED,
                discovered_via=DiscoverySource.MANUAL,
            )
        )
        await session.flush()
        session.add(RawMessage(channel_id=3, msg_id=10, payload={"id": 10}))
        session.add(video)
        await session.flush()
        session.add(message_embedding)
        session.add(video_embedding)
    return video, message_embedding


async def test_a_message_that_goes_takes_its_embedding(
    database: Database,
) -> None:
    await _seed(database)

    async with database.session() as session:
        await session.execute(delete(RawMessage))

    assert await _count(database, TgPostEmbedding) == 0


async def test_an_embedding_without_its_message_is_refused(
    database: Database,
) -> None:
    with pytest.raises(IntegrityError):
        async with database.session() as session:
            session.add(
                TgPostEmbedding(
                    channel_id=3, msg_id=10, model_key="m", vec=b"\x00\x01"
                )
            )


async def test_a_video_that_goes_takes_its_embedding(
    database: Database,
) -> None:
    await _seed(database)

    async with database.session() as session:
        await session.execute(delete(YtRawVideo))

    assert await _count(database, YtVideoEmbedding) == 0


async def test_an_embedding_without_its_video_is_refused(
    database: Database,
) -> None:
    with pytest.raises(IntegrityError):
        async with database.session() as session:
            session.add(
                YtVideoEmbedding(
                    video_id="dQw4w9WgXcQ",
                    model_key="m",
                    content_hash="abc123",
                    vec=b"\x00\x01",
                )
            )


async def test_one_vector_per_text_even_under_another_model(
    database: Database,
) -> None:
    video, _ = await _seed(database)

    with pytest.raises(IntegrityError):
        async with database.session() as session:
            session.add(
                YtVideoEmbedding(
                    video_id=video.video_id,
                    model_key="another-model",
                    content_hash="abc123",
                    vec=b"\x00\x02",
                )
            )
