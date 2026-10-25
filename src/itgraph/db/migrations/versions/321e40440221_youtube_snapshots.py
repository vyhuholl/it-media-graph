"""youtube snapshots

Revision ID: 321e40440221
Revises: eccc180aac8e
Create Date: 2026-10-26 01:19:03.515609

The YouTube poll loop's tables. Two are API data and carry the `yt_raw_`
prefix, which is what keeps their rows out of every dump: a video's
latest payload, with the schedule the loop works out from it, and the
readings of its counters. Two are the loop's own — where it stands with
each channel, and what each pass did — and hold no API data.

Also trims stored channel payloads to the fields the privacy policy
lists. Change 1 asked for branding, status and topic data that no code
read and the page never mentioned; from this revision every request
names its fields, and the payloads already stored are made to match on
the day it is deployed rather than at their next refresh. One way: the
keys dropped are API data nothing reads.

"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

# revision identifiers, used by Alembic.
revision: str = "321e40440221"
down_revision: str | Sequence[str] | None = "eccc180aac8e"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

YT_DISCOVERY_PATH = postgresql.ENUM(
    "first", "count", "check", name="yt_discovery_path"
)
YT_PASS_OUTCOME = postgresql.ENUM(
    "complete", "pool_spent", "exhausted", "failed", name="yt_pass_outcome"
)
NEW_TYPES = (YT_DISCOVERY_PATH, YT_PASS_OUTCOME)

# The declared channel fields, rebuilt from what is stored. The outer
# `jsonb_strip_nulls` works recursively, so a field a channel never had
# stays absent rather than becoming a null. Kept as a constant so the
# test suite can run exactly this statement against a payload of the
# old shape.
TRIM_CHANNEL_PAYLOADS = """
UPDATE yt_raw_channels SET payload = jsonb_strip_nulls(jsonb_build_object(
    'id', payload -> 'id',
    'snippet', jsonb_build_object(
        'title', payload #> '{snippet,title}',
        'description', payload #> '{snippet,description}',
        'customUrl', payload #> '{snippet,customUrl}',
        'country', payload #> '{snippet,country}',
        'defaultLanguage', payload #> '{snippet,defaultLanguage}'
    ),
    'contentDetails', jsonb_build_object(
        'relatedPlaylists', jsonb_build_object(
            'uploads', payload #> '{contentDetails,relatedPlaylists,uploads}'
        )
    ),
    'statistics', jsonb_build_object(
        'viewCount', payload #> '{statistics,viewCount}',
        'subscriberCount', payload #> '{statistics,subscriberCount}',
        'hiddenSubscriberCount', payload #> '{statistics,hiddenSubscriberCount}',
        'videoCount', payload #> '{statistics,videoCount}'
    )
))
"""


def _column_type(type_: postgresql.ENUM) -> postgresql.ENUM:
    """A type created above, as a column refers to it: without creating it."""
    return postgresql.ENUM(*type_.enums, name=type_.name, create_type=False)


def upgrade() -> None:
    """Upgrade schema."""
    bind = op.get_bind()
    for type_ in NEW_TYPES:
        type_.create(bind)

    op.create_table(
        "yt_channel_polls",
        sa.Column("channel_id", sa.Text(), nullable=False),
        sa.Column("listed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column(
            "list_owed",
            sa.Boolean(),
            server_default=sa.text("false"),
            nullable=False,
        ),
        sa.Column("attempted_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column(
            "consecutive_failures",
            sa.Integer(),
            server_default=sa.text("0"),
            nullable=False,
        ),
        sa.Column("last_error", sa.Text(), nullable=True),
        sa.ForeignKeyConstraint(
            ["channel_id"],
            ["yt_channels.channel_id"],
            name=op.f("fk_yt_channel_polls_channel_id_yt_channels"),
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint(
            "channel_id", name=op.f("pk_yt_channel_polls")
        ),
    )

    op.create_table(
        "yt_raw_videos",
        sa.Column("video_id", sa.Text(), nullable=False),
        sa.Column(
            "channel_id",
            sa.Text(),
            sa.Computed("payload #>> '{snippet,channelId}'", persisted=True),
            nullable=True,
        ),
        sa.Column(
            "payload",
            postgresql.JSONB(astext_type=sa.Text()),
            nullable=False,
        ),
        sa.Column("fetched_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("discovered_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column(
            "discovered_via", _column_type(YT_DISCOVERY_PATH), nullable=False
        ),
        sa.Column("due_at", sa.DateTime(timezone=True), nullable=True),
        sa.CheckConstraint(
            "video_id ~ '^[0-9A-Za-z_-]{11}$'",
            name=op.f("ck_yt_raw_videos_id_shape"),
        ),
        sa.PrimaryKeyConstraint("video_id", name=op.f("pk_yt_raw_videos")),
    )
    op.create_index(
        "ix_yt_raw_videos_channel_id", "yt_raw_videos", ["channel_id"]
    )
    op.create_index(
        "ix_yt_raw_videos_due_at",
        "yt_raw_videos",
        ["due_at"],
        postgresql_where=sa.text("due_at IS NOT NULL"),
    )
    op.create_index(
        "ix_yt_raw_videos_fetched_at", "yt_raw_videos", ["fetched_at"]
    )

    op.create_table(
        "yt_raw_video_stats",
        sa.Column("video_id", sa.Text(), nullable=False),
        sa.Column("observed_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("views", sa.BigInteger(), nullable=True),
        sa.Column("likes", sa.BigInteger(), nullable=True),
        sa.Column("comments", sa.BigInteger(), nullable=True),
        sa.ForeignKeyConstraint(
            ["video_id"],
            ["yt_raw_videos.video_id"],
            name=op.f("fk_yt_raw_video_stats_video_id_yt_raw_videos"),
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint(
            "video_id", "observed_at", name=op.f("pk_yt_raw_video_stats")
        ),
    )
    op.create_index(
        "ix_yt_raw_video_stats_observed_at",
        "yt_raw_video_stats",
        ["observed_at"],
    )

    op.create_table(
        "yt_watch_passes",
        sa.Column("id", sa.BigInteger(), nullable=False),
        sa.Column("started_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("finished_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("outcome", _column_type(YT_PASS_OUTCOME), nullable=False),
        *(
            sa.Column(
                name, sa.Integer(), server_default=sa.text("0"), nullable=False
            )
            for name in (
                "channels",
                "listed",
                "list_failures",
                "new_videos",
                "readings",
                "removed",
                "deferred_checks",
                "deferred_readings",
            )
        ),
        sa.Column("error", sa.Text(), nullable=True),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_yt_watch_passes")),
    )
    op.create_index(
        "ix_yt_watch_passes_started_at", "yt_watch_passes", ["started_at"]
    )

    op.execute(TRIM_CHANNEL_PAYLOADS)


def downgrade() -> None:
    """Downgrade schema.

    Drops the four tables and their two types. The trim is not undone:
    the keys it dropped were API data nothing read, and the next fetch
    under the old code brings them back.
    """
    op.drop_index(
        "ix_yt_watch_passes_started_at", table_name="yt_watch_passes"
    )
    op.drop_table("yt_watch_passes")
    op.drop_index(
        "ix_yt_raw_video_stats_observed_at", table_name="yt_raw_video_stats"
    )
    op.drop_table("yt_raw_video_stats")
    op.drop_index("ix_yt_raw_videos_fetched_at", table_name="yt_raw_videos")
    op.drop_index("ix_yt_raw_videos_due_at", table_name="yt_raw_videos")
    op.drop_index("ix_yt_raw_videos_channel_id", table_name="yt_raw_videos")
    op.drop_table("yt_raw_videos")
    op.drop_table("yt_channel_polls")

    bind = op.get_bind()
    for type_ in reversed(NEW_TYPES):
        type_.drop(bind)
