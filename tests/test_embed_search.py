"""Ranking both platforms against one query, with vectors the test names.

What is pinned: the cosine order (not the insertion order) decides, a
foreign model's vectors are not ranked, links take the username form
where there is a username and the ``t.me/c/…`` form where there is
none, and a database with nothing embedded is a clean emptiness, not an
error.
"""

from datetime import UTC, datetime

import pytest
from embedfakes import FakeEmbeddingModel, vector
from ytfakes import channel_id

from itgraph.db.models import (
    Channel,
    ChannelStatus,
    DiscoverySource,
    RawMessage,
    TgPostEmbedding,
    YtChannel,
    YtChannelKind,
    YtDiscoveryPath,
    YtDiscoverySource,
    YtLang,
    YtRawChannel,
    YtRawVideo,
    YtVideoEmbedding,
)
from itgraph.db.session import Database
from itgraph.embed.search import TELEGRAM, YOUTUBE, search

NOW = datetime(2026, 11, 21, 12, 0, tzinfo=UTC)
YT_CHANNEL = channel_id(1)


def _message(channel_id: int, msg_id: int, body: str) -> RawMessage:
    return RawMessage(
        channel_id=channel_id,
        msg_id=msg_id,
        payload={
            "id": msg_id,
            "message": body,
            "date": "2026-11-20T09:00:00+00:00",
        },
    )


def _video(number: int, *, title: str, description: str = "") -> YtRawVideo:
    payload = {
        "snippet": {
            "title": title,
            "description": description,
            "channelId": YT_CHANNEL,
            "publishedAt": "2026-11-19T18:00:00+00:00",
        }
    }
    return YtRawVideo(
        video_id=f"fakevideo{number:02d}",
        payload=payload,
        fetched_at=NOW,
        discovered_at=NOW,
        discovered_via=YtDiscoveryPath.FIRST,
        due_at=None,
    )


async def _seed(database: Database) -> None:
    """Two named channels, an unnamed one, two videos, one channel."""
    async with database.session() as session:
        session.add_all(
            [
                Channel(
                    tg_id=3,
                    username="fake_named",
                    title="Named Channel",
                    status=ChannelStatus.SEED,
                    discovered_via=DiscoverySource.MANUAL,
                ),
                Channel(
                    tg_id=4,
                    status=ChannelStatus.SEED,
                    discovered_via=DiscoverySource.MANUAL,
                ),
            ]
        )
        await session.flush()
        session.add(_message(3, 10, "the post that matches"))
        session.add(_message(3, 11, "the mediocre middle"))
        session.add(_message(4, 20, "a post of the unnamed channel"))
        session.add(_message(4, 21, "a post under the old model"))
        session.add(_video(1, title="The Video That Matches"))
        session.add(_video(2, title="The Unrelated Video"))
        session.add(
            YtChannel(
                channel_id=YT_CHANNEL,
                status=ChannelStatus.SEED,
                kind=YtChannelKind.PERSONAL,
                lang=YtLang.EN,
                discovered_via=YtDiscoverySource.MANUAL,
            )
        )
        await session.flush()
        session.add(
            YtRawChannel(
                channel_id=YT_CHANNEL,
                payload={"snippet": {"title": "Fake YouTube Channel"}},
                fetched_at=NOW,
            )
        )
        await session.flush()
        session.add_all(
            [
                # (1, 0) is the query's own direction; (0.6, 0.8) leans
                # towards it; (0, 1) is orthogonal.
                TgPostEmbedding(
                    channel_id=3,
                    msg_id=10,
                    model_key="multilingual-e5-small-o4",
                    vec=vector(1.0),
                ),
                TgPostEmbedding(
                    channel_id=3,
                    msg_id=11,
                    model_key="multilingual-e5-small-o4",
                    vec=vector(0.6, 0.8),
                ),
                TgPostEmbedding(
                    channel_id=4,
                    msg_id=20,
                    model_key="multilingual-e5-small-o4",
                    vec=vector(0.0, 1.0),
                ),
                # A foreign model's vector: perfectly aligned with the
                # query, and not ranked for exactly that reason.
                TgPostEmbedding(
                    channel_id=4,
                    msg_id=21,
                    model_key="the-old-model",
                    vec=vector(1.0),
                ),
                YtVideoEmbedding(
                    video_id="fakevideo01",
                    model_key="multilingual-e5-small-o4",
                    content_hash="a" * 64,
                    vec=vector(0.6, 0.8),
                ),
                YtVideoEmbedding(
                    video_id="fakevideo02",
                    model_key="multilingual-e5-small-o4",
                    content_hash="b" * 64,
                    vec=vector(0.0, 1.0),
                ),
            ]
        )


def _model() -> FakeEmbeddingModel:
    return FakeEmbeddingModel(query=vector(1.0))


async def test_both_platforms_rank_together_by_cosine(
    database: Database,
) -> None:
    await _seed(database)

    hits = await search(database, _model(), "what the query is about")

    # The middle two share a vector, so they share a score, and the
    # stable sort keeps Telegram before YouTube at a tie.
    assert [(hit.platform, hit.link) for hit in hits] == [
        (TELEGRAM, "https://t.me/fake_named/10"),
        (TELEGRAM, "https://t.me/fake_named/11"),
        (YOUTUBE, "https://www.youtube.com/watch?v=fakevideo01"),
        (TELEGRAM, "https://t.me/c/4/20"),
        (YOUTUBE, "https://www.youtube.com/watch?v=fakevideo02"),
    ]
    assert hits[0].score == pytest.approx(1.0, abs=1e-3)
    assert hits[-1].score == pytest.approx(0.0, abs=1e-3)


async def test_hits_carry_channel_date_and_text_head(
    database: Database,
) -> None:
    await _seed(database)

    hits = await search(database, _model(), "what the query is about")
    best = hits[0]

    assert best.channel == "Named Channel"
    assert best.date == "2026-11-20"
    assert best.body_head == "the post that matches"

    video = hits[2]
    assert video.channel == "Fake YouTube Channel"
    assert video.date == "2026-11-19"
    assert video.body_head == "The Video That Matches"

    unnamed = hits[3]
    assert unnamed.channel == "4"  # no title, no username: the id
    assert unnamed.link == "https://t.me/c/4/20"


async def test_top_bounds_the_list(database: Database) -> None:
    await _seed(database)

    hits = await search(database, _model(), "anything", top=2)

    assert [hit.link for hit in hits] == [
        "https://t.me/fake_named/10",
        "https://t.me/fake_named/11",
    ]
