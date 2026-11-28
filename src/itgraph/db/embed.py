"""The embedding job: what is due, embedded, and written back.

Every run does the same thing — select up to a batch of texts whose
embedding is missing, whose ``model_key`` is not the configured model,
or (YouTube only) whose text has changed since it was embedded; embed;
upsert; commit; repeat until nothing is due. The first run on a
database is therefore the backfill, an interrupted run is resumed by
the next, and a model change re-embeds the corpus with no special mode.

The YouTube join and its hash are stated here in SQL, so due rows are
found without shipping every payload to Python.
"""

from dataclasses import dataclass

from sqlalchemy import func, text
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.ext.asyncio import AsyncSession

from itgraph.db.models import TgPostEmbedding, YtVideoEmbedding
from itgraph.db.session import Database
from itgraph.embed.model import MODEL_KEY, EmbeddingModel

# The design's batch: minutes of work on four threads, one commit's
# worth of loss on an interrupt — and a memory the job's limit carries:
# measured on the collection host, the loaded model takes 675 MiB and
# each text in the batch ~22 MiB more, so 256 peaked at 9.1 GiB and was
# OOM-killed inside the 4 Gi the job allows, while 64 stays under
# 2.1 GiB.
BATCH = 64

# `yt_text` for Postgres: the title alone when there is no description,
# title, blank line, description when there is. The escaped blank line
# is a SQL literal — backslash-n inside E'…', not a newline in Python.
_YT_TEXT = (
    "CASE WHEN COALESCE(v.payload #>> '{snippet,description}', '') = '' "
    "THEN v.payload #>> '{snippet,title}' "
    "ELSE (v.payload #>> '{snippet,title}') || E'\\n\\n' "
    "|| (v.payload #>> '{snippet,description}') END"
)

# `content_hash` for Postgres: sha256 over the UTF-8 bytes, hex.
_YT_HASH = f"encode(sha256(convert_to({_YT_TEXT}, 'UTF8')), 'hex')"

_DUE_POSTS = text(
    """
    SELECT m.channel_id AS channel_id, m.msg_id AS msg_id,
           m.payload ->> 'message' AS body
      FROM tg_raw_messages m
      LEFT JOIN tg_post_embeddings e
             ON e.channel_id = m.channel_id AND e.msg_id = m.msg_id
     WHERE COALESCE(m.payload ->> 'message', '') <> ''
       AND (e.channel_id IS NULL OR e.model_key != :model_key)
     ORDER BY m.channel_id, m.msg_id
     LIMIT :batch
    """
)

_DUE_VIDEOS = text(
    f"""
    SELECT v.video_id AS video_id, {_YT_TEXT} AS body,
           {_YT_HASH} AS content_hash
      FROM yt_raw_videos v
      LEFT JOIN yt_video_embeddings e ON e.video_id = v.video_id
     WHERE COALESCE(v.payload #>> '{{snippet,title}}', '') <> ''
       AND (e.video_id IS NULL OR e.model_key != :model_key
            OR e.content_hash != {_YT_HASH})
     ORDER BY v.video_id
     LIMIT :batch
    """
)


@dataclass(frozen=True)
class DuePost:
    """A Telegram text to embed, addressed by its raw row."""

    channel_id: int
    msg_id: int
    body: str


@dataclass(frozen=True)
class DueVideo:
    """A YouTube text to embed, with the hash the selection computed."""

    video_id: str
    body: str
    content_hash: str


@dataclass(frozen=True)
class EmbedSummary:
    """What one run embedded, as the command's one line."""

    posts: int
    videos: int

    def line(self) -> str:
        return (
            f"embedded {self.posts} Telegram and {self.videos} YouTube "
            f"texts ({MODEL_KEY})"
        )


async def due_posts(
    session: AsyncSession, model_key: str, batch: int
) -> list[DuePost]:
    """Up to ``batch`` Telegram texts whose embedding is missing or stale."""
    rows = await session.execute(
        _DUE_POSTS, {"model_key": model_key, "batch": batch}
    )
    return [
        DuePost(channel_id=row.channel_id, msg_id=row.msg_id, body=row.body)
        for row in rows
    ]


async def due_videos(
    session: AsyncSession, model_key: str, batch: int
) -> list[DueVideo]:
    """Up to ``batch`` YouTube texts due: missing, stale, or changed."""
    rows = await session.execute(
        _DUE_VIDEOS, {"model_key": model_key, "batch": batch}
    )
    return [
        DueVideo(
            video_id=row.video_id, body=row.body, content_hash=row.content_hash
        )
        for row in rows
    ]


async def _upsert_posts(
    session: AsyncSession,
    due: list[DuePost],
    vecs: list[bytes],
    model_key: str,
) -> None:
    statement = insert(TgPostEmbedding).values(
        [
            {
                "channel_id": row.channel_id,
                "msg_id": row.msg_id,
                "model_key": model_key,
                "vec": vec,
            }
            for row, vec in zip(due, vecs, strict=True)
        ]
    )
    statement = statement.on_conflict_do_update(
        index_elements=[TgPostEmbedding.channel_id, TgPostEmbedding.msg_id],
        set_={
            "model_key": statement.excluded.model_key,
            "vec": statement.excluded.vec,
            # The update path names it because the column's default
            # speaks only on insert: a re-embed is a new embedding, and
            # an old timestamp on a new vector is a lie.
            "embedded_at": func.now(),
        },
    )
    await session.execute(statement)


async def _upsert_videos(
    session: AsyncSession,
    due: list[DueVideo],
    vecs: list[bytes],
    model_key: str,
) -> None:
    statement = insert(YtVideoEmbedding).values(
        [
            {
                "video_id": row.video_id,
                "model_key": model_key,
                "content_hash": row.content_hash,
                "vec": vec,
            }
            for row, vec in zip(due, vecs, strict=True)
        ]
    )
    statement = statement.on_conflict_do_update(
        index_elements=[YtVideoEmbedding.video_id],
        set_={
            "model_key": statement.excluded.model_key,
            "content_hash": statement.excluded.content_hash,
            "vec": statement.excluded.vec,
            "embedded_at": func.now(),
        },
    )
    await session.execute(statement)


async def run_embed(
    database: Database,
    model: EmbeddingModel,
    *,
    model_key: str = MODEL_KEY,
    batch: int = BATCH,
) -> EmbedSummary:
    """Embed everything due, one committed batch at a time.

    A batch is lost, not the run, when the process dies mid-loop: the
    next run's selection skips what the last commit covered. The two
    platforms interleave, so a large Telegram backfill does not hold the
    day's YouTube texts behind it.
    """
    posts = 0
    videos = 0
    async with database.session() as session:
        while True:
            due_tg = await due_posts(session, model_key, batch)
            if due_tg:
                vecs = model.embed_passages([row.body for row in due_tg])
                await _upsert_posts(session, due_tg, vecs, model_key)
                await session.commit()
                posts += len(due_tg)
            due_yt = await due_videos(session, model_key, batch)
            if due_yt:
                vecs = model.embed_passages([row.body for row in due_yt])
                await _upsert_videos(session, due_yt, vecs, model_key)
                await session.commit()
                videos += len(due_yt)
            if not due_tg and not due_yt:
                return EmbedSummary(posts=posts, videos=videos)
