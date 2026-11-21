"""The embed loop's due-selection and upserts, against the real schema.

The four behaviours the spec names, each with its own test: a nightly
increment embeds only what arrived, an interrupted backfill resumes
where its last commit stopped, a model change re-embeds everything
under the new name, and a YouTube text whose hash did not change is
left alone while one whose did is re-embedded — still one row.
"""

from datetime import UTC, datetime

from embedfakes import FakeEmbeddingModel, FlakyModel

from itgraph.db.embed import run_embed
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
from itgraph.embed.model import MODEL_KEY

NOW = datetime(2026, 11, 21, 12, 0, tzinfo=UTC)
OTHER_MODEL = "another-model-entirely"


def _message(msg_id: int, body: str) -> RawMessage:
    return RawMessage(
        channel_id=3,
        msg_id=msg_id,
        payload={
            "id": msg_id,
            "message": body,
            "date": "2026-11-20T09:00:00+00:00",
        },
    )


def _video_payload(
    *, title: str | None, description: str | None
) -> dict[str, object]:
    snippet: dict[str, object] = {"title": title, "publishedAt": "2026-11-20"}
    if description is not None:
        snippet["description"] = description
    return {"snippet": snippet}


def _video(number: int, payload: dict[str, object]) -> YtRawVideo:
    return YtRawVideo(
        video_id=f"fakevideo{number:02d}",
        payload=payload,
        fetched_at=NOW,
        discovered_at=NOW,
        discovered_via=YtDiscoveryPath.FIRST,
        due_at=None,
    )


async def _seed_base(database: Database) -> None:
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
        session.add(_message(10, "первый пост"))
        session.add(_message(11, "второй пост"))
        session.add(_message(12, "третий пост"))
        session.add(RawMessage(channel_id=3, msg_id=13, payload={"id": 13}))


async def _embeddings(
    database: Database,
) -> tuple[list[tuple[int, int, str]], list[tuple[str, str, str]]]:
    """Every embedding row as (post keys, model) and (video, model, hash)."""
    async with database.session() as session:
        posts = (
            await session.execute(TgPostEmbedding.__table__.select())
        ).all()
        videos = (
            await session.execute(YtVideoEmbedding.__table__.select())
        ).all()
    return (
        [(row.channel_id, row.msg_id, row.model_key) for row in posts],
        [(row.video_id, row.model_key, row.content_hash) for row in videos],
    )


async def test_the_nightly_increment_embeds_only_what_arrived(
    database: Database,
) -> None:
    await _seed_base(database)
    first = FakeEmbeddingModel()

    summary = await run_embed(database, first)

    assert summary.posts == 3  # the media-only message is not a text
    assert sorted(first.passages_seen) == [
        "второй пост",
        "первый пост",
        "третий пост",
    ]

    arrived = FakeEmbeddingModel()
    async with database.session() as session:
        session.add(_message(14, "новый пост"))
    second = await run_embed(database, arrived)

    assert second.posts == 1
    assert arrived.passages_seen == ["новый пост"]
    posts, _ = await _embeddings(database)
    assert len(posts) == 4


async def test_an_interrupted_backfill_resumes_at_its_last_commit(
    database: Database,
) -> None:
    await _seed_base(database)
    async with database.session() as session:
        session.add(_message(14, "четвёртый"))
        session.add(_message(15, "пятый"))
    interrupted = FlakyModel(FakeEmbeddingModel(), calls_before_failure=1)

    try:
        await run_embed(database, interrupted, batch=2)
    except RuntimeError:
        pass
    else:
        raise AssertionError("the interruption never arrived")

    posts, _ = await _embeddings(database)
    assert len(posts) == 2  # one committed batch, no more

    resumed = FakeEmbeddingModel()
    summary = await run_embed(database, resumed, batch=2)

    assert summary.posts == 3
    assert len(resumed.passages_seen) == 3
    posts, _ = await _embeddings(database)
    assert len(posts) == 5


async def test_a_model_change_re_embeds_under_the_new_name(
    database: Database,
) -> None:
    await _seed_base(database)
    await run_embed(database, FakeEmbeddingModel())

    changed = FakeEmbeddingModel()
    summary = await run_embed(database, changed, model_key=OTHER_MODEL)

    assert summary.posts == 3
    assert len(changed.passages_seen) == 3
    posts, _ = await _embeddings(database)
    assert posts and all(model == OTHER_MODEL for _, _, model in posts)


async def test_an_unchanged_youtube_text_is_left_alone(
    database: Database,
) -> None:
    await _seed_base(database)
    async with database.session() as session:
        session.add(
            _video(
                1,
                _video_payload(
                    title="Stable Title", description="Stable description."
                ),
            )
        )
    await run_embed(database, FakeEmbeddingModel())

    again = FakeEmbeddingModel()
    summary = await run_embed(database, again)

    # The posts are done too; only that no video text came back due.
    assert summary.videos == 0
    assert all("Stable" not in text for text in again.passages_seen)
    _, videos = await _embeddings(database)
    assert len(videos) == 1


async def test_a_changed_description_re_embeds_one_row(
    database: Database,
) -> None:
    await _seed_base(database)
    async with database.session() as session:
        session.add(
            _video(
                1,
                _video_payload(title="Same Title", description="First take."),
            )
        )
        session.add(
            _video(
                2,
                _video_payload(title="Untouched", description="Same forever."),
            )
        )
    await run_embed(database, FakeEmbeddingModel())

    # The refresh: video 1's description changed, video 2's did not.
    async with database.session() as session:
        await session.execute(
            YtRawVideo.__table__.update()
            .where(YtRawVideo.video_id == "fakevideo01")
            .values(
                payload=_video_payload(
                    title="Same Title", description="Second take."
                ),
                fetched_at=NOW,
            )
        )
    edited = FakeEmbeddingModel()
    summary = await run_embed(database, edited)

    assert summary.videos == 1
    assert edited.passages_seen == ["Same Title\n\nSecond take."]
    _, videos = await _embeddings(database)
    assert len(videos) == 2
    hashes = {video_id: content_hash for video_id, _, content_hash in videos}
    assert hashes["fakevideo01"] != hashes["fakevideo02"]


async def test_every_row_names_its_model(database: Database) -> None:
    await _seed_base(database)

    await run_embed(database, FakeEmbeddingModel())

    posts, videos = await _embeddings(database)
    assert posts and all(model == MODEL_KEY for _, _, model in posts)
    assert videos == []


async def test_a_video_without_a_title_is_not_embedded(
    database: Database,
) -> None:
    await _seed_base(database)
    async with database.session() as session:
        session.add(
            _video(9, _video_payload(title=None, description="orphan body"))
        )

    model = FakeEmbeddingModel()
    summary = await run_embed(database, model)

    assert summary.videos == 0
    assert "orphan body" not in model.passages_seen
    _, videos = await _embeddings(database)
    assert videos == []
