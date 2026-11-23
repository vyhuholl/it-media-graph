"""Ranking both platforms' texts against one query.

The query is embedded with the configured model, both tables' vectors
made by that model are loaded with the metadata a result line needs,
and the ranking is one cosine — numpy over the whole corpus, brute
force, single-digit milliseconds at this scale. Vectors of a different
``model_key`` are not ranked: they are not in the query's space, and
the job's next run re-embeds them anyway.
"""

from collections.abc import Sequence
from dataclasses import dataclass

import numpy as np
from numpy.typing import NDArray
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from itgraph.db.session import Database
from itgraph.embed.model import MODEL_KEY, EmbeddingModel

TELEGRAM = "telegram"
YOUTUBE = "youtube"

# How much of a text a result line shows. A head, not the whole text:
# the line exists to recognise the post, not to read it.
TEXT_HEAD = 80

_POST_VECTORS = text(
    """
    SELECT e.channel_id AS channel_id, e.msg_id AS msg_id, e.vec AS vec,
           c.username AS username,
           COALESCE(c.title, c.username, c.tg_id::text) AS channel,
           m.payload ->> 'date' AS date,
           left(m.payload ->> 'message', 200) AS body
      FROM tg_post_embeddings e
      JOIN tg_raw_messages m
        ON m.channel_id = e.channel_id AND m.msg_id = e.msg_id
      LEFT JOIN tg_channels c ON c.tg_id = e.channel_id
     WHERE e.model_key = :model_key
    """
)

_VIDEO_VECTORS = text(
    """
    SELECT e.video_id AS video_id, e.vec AS vec,
           COALESCE(rc.payload #>> '{snippet,title}',
                    v.payload #>> '{snippet,channelId}') AS channel,
           v.payload #>> '{snippet,publishedAt}' AS date,
           left(v.payload #>> '{snippet,title}', 200) AS body
      FROM yt_video_embeddings e
      JOIN yt_raw_videos v ON v.video_id = e.video_id
      LEFT JOIN yt_raw_channels rc
        ON rc.channel_id = v.payload #>> '{snippet,channelId}'
     WHERE e.model_key = :model_key
    """
)


@dataclass(frozen=True)
class Hit:
    """One ranked text, with everything its line prints."""

    platform: str
    channel: str
    date: str
    body_head: str
    link: str
    score: float


@dataclass(frozen=True)
class _Loaded:
    """One platform's rows, in load order: vector scores and line parts."""

    scores: NDArray[np.float32]
    channels: list[str]
    dates: list[str]
    bodies: list[str]
    links: list[str]


def post_link(channel_id: int, msg_id: int, username: str | None) -> str:
    """A message's address: by username where the channel has one.

    The ``t.me/c/<id>/…`` form reaches channels without a username, and
    its id is the bare channel id Telethon reports — the form this
    project stores — not the ``-100``-prefixed one.
    """
    if username:
        return f"https://t.me/{username}/{msg_id}"
    return f"https://t.me/c/{channel_id}/{msg_id}"


def video_link(video_id: str) -> str:
    return f"https://www.youtube.com/watch?v={video_id}"


def _head(body: str | None) -> str:
    """The first line's worth of a text, on one line."""
    flat = " ".join((body or "").split())
    return flat[:TEXT_HEAD]


def _decode(blob: bytes) -> NDArray[np.float32]:
    """fp16 little-endian bytes back to a unit float32 vector."""
    vector = np.frombuffer(blob, dtype="<f2").astype(np.float32)
    # Re-normalised rather than trusted: the stored vector was unit
    # before float16 rounded it, and a dot product wants it unit after.
    return vector / np.linalg.norm(vector)


def _scores(
    blobs: Sequence[bytes], query: NDArray[np.float32]
) -> NDArray[np.float32]:
    """Every blob's cosine against the query, in load order.

    One preallocated matrix, not a list of per-row arrays stacked into
    a second copy: at the corpus's size the list-plus-stack held the
    vectors twice, and the console's Job died on that duplicate. The
    width comes off the blobs — one model's vectors share a dimension,
    and which one it is is the table's business, not the ranking's.
    """
    if not blobs:
        return np.empty(0, dtype=np.float32)
    vectors = np.empty((len(blobs), len(blobs[0]) // 2), dtype=np.float32)
    for place, blob in enumerate(blobs):
        vectors[place] = np.frombuffer(blob, dtype="<f2")
    # Re-normalised rather than trusted: the stored vector was unit
    # before float16 rounded it, and a dot product wants it unit after.
    vectors /= np.linalg.norm(vectors, axis=1, keepdims=True)
    return vectors @ query


def _hits(loaded: _Loaded, platform: str, top: int) -> list[Hit]:
    """A platform's best ``top`` rows as hits, best first."""
    order = np.argsort(loaded.scores)[::-1][:top]
    return [
        Hit(
            platform=platform,
            channel=loaded.channels[index],
            date=loaded.dates[index],
            body_head=_head(loaded.bodies[index]),
            link=loaded.links[index],
            score=float(loaded.scores[index]),
        )
        for index in order
    ]


async def _telegram(
    session: AsyncSession, model_key: str, query: NDArray[np.float32]
) -> _Loaded:
    rows = (
        await session.execute(_POST_VECTORS, {"model_key": model_key})
    ).all()
    return _Loaded(
        scores=_scores([row.vec for row in rows], query),
        channels=[row.channel for row in rows],
        dates=[(row.date or "")[:10] for row in rows],
        bodies=[row.body or "" for row in rows],
        links=[
            post_link(row.channel_id, row.msg_id, row.username) for row in rows
        ],
    )


async def _youtube(
    session: AsyncSession, model_key: str, query: NDArray[np.float32]
) -> _Loaded:
    rows = (
        await session.execute(_VIDEO_VECTORS, {"model_key": model_key})
    ).all()
    return _Loaded(
        scores=_scores([row.vec for row in rows], query),
        channels=[row.channel for row in rows],
        dates=[(row.date or "")[:10] for row in rows],
        bodies=[row.body or "" for row in rows],
        links=[video_link(row.video_id) for row in rows],
    )


async def search(
    database: Database,
    model: EmbeddingModel,
    query: str,
    *,
    top: int = 20,
    model_key: str = MODEL_KEY,
) -> list[Hit]:
    """Both platforms' texts ranked against ``query``, best first.

    Empty when there is nothing to search — no vector made by the
    configured model — which is a clean state, not an error.
    """
    vector = _decode(model.embed_query(query))
    async with database.session() as session:
        telegram = await _telegram(session, model_key, vector)
        youtube = await _youtube(session, model_key, vector)
    ranked = sorted(
        _hits(telegram, TELEGRAM, top) + _hits(youtube, YOUTUBE, top),
        key=lambda hit: hit.score,
        reverse=True,
    )
    return ranked[:top]
