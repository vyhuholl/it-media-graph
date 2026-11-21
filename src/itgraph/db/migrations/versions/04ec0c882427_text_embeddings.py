"""text embeddings

Revision ID: 04ec0c882427
Revises: c96f3ea41b7d
Create Date: 2026-11-21 18:00:00.000000

A vector over what a post or a video is *about*, beside the structural
and interaction axes: one row per text, the vector as `bytea` float16
little-endian, the model named by `model_key` so a model change is a
re-embed of every row and never a migration. Nothing checks the byte
length — the dimension belongs to the named model, and an upgrade to a
wider one re-embeds under a new key rather than fighting a constraint.

Telegram's texts are immutable in the raw layer (the first fetch wins),
so `tg_post_embeddings` embeds each once, keyed by the raw row it
derives from. YouTube's raw layer is refreshed, not frozen, so
`yt_video_embeddings` carries a `content_hash` of the joined
title+description it was made from: a refresh that changes the text
re-embeds it through the job's ordinary stale check, still one row per
video.

Both foreign keys cascade, mirroring `yt_raw_video_stats`: a video
YouTube stops returning takes its embedding with it, and a Telegram
message has no deletion path today. Nothing is backfilled here — the
first `itgraph embed` run fills both tables from the texts the raw
layer already holds, and everything derived is re-runnable from them.

**The grants.** No workload reads these tables yet: search runs as the
console's own credential, like every operator command. The reader takes
both tables through its default privileges.

`drop_table` here leaves nothing behind: the tables use no enum types
of their own.
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision: str = "04ec0c882427"
down_revision: str | Sequence[str] | None = "c96f3ea41b7d"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """Upgrade schema."""
    op.create_table(
        "tg_post_embeddings",
        sa.Column("channel_id", sa.BigInteger(), nullable=False),
        sa.Column("msg_id", sa.BigInteger(), nullable=False),
        sa.Column("model_key", sa.Text(), nullable=False),
        sa.Column("vec", sa.LargeBinary(), nullable=False),
        sa.Column(
            "embedded_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.ForeignKeyConstraint(
            ["channel_id", "msg_id"],
            ["tg_raw_messages.channel_id", "tg_raw_messages.msg_id"],
            name=op.f("fk_tg_post_embeddings_channel_id_tg_raw_messages"),
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint(
            "channel_id", "msg_id", name=op.f("pk_tg_post_embeddings")
        ),
    )
    op.create_table(
        "yt_video_embeddings",
        sa.Column("video_id", sa.Text(), nullable=False),
        sa.Column("model_key", sa.Text(), nullable=False),
        sa.Column("content_hash", sa.Text(), nullable=False),
        sa.Column("vec", sa.LargeBinary(), nullable=False),
        sa.Column(
            "embedded_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.ForeignKeyConstraint(
            ["video_id"],
            ["yt_raw_videos.video_id"],
            name=op.f("fk_yt_video_embeddings_video_id_yt_raw_videos"),
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint(
            "video_id", name=op.f("pk_yt_video_embeddings")
        ),
    )


def downgrade() -> None:
    """Downgrade schema.

    The rows are a pure derivation of texts the raw layer still holds,
    so dropping them loses nothing a later `itgraph embed` run does not
    rebuild.
    """
    op.drop_table("yt_video_embeddings")
    op.drop_table("tg_post_embeddings")
