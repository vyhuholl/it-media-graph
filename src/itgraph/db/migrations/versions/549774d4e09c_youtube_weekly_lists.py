"""youtube weekly lists

Revision ID: 549774d4e09c
Revises: 5e7cef4aa5d0
Create Date: 2026-11-12 06:36:50.245524

Each language's list of what took off on YouTube in a week: a list per
week and route, and an entry per channel naming its best video. Two
existing types are reused, `yt_alert_route` and `yt_metric`.

**An entry lives as long as the reading its score came from**, by a
foreign key with `ON DELETE CASCADE` to `yt_raw_video_stats`, as an
alert does. The list row holds no video and stays.

**The grants.** The bot reads both tables and may change only their
delivery state: not write a list, and not touch an entry's rank, video,
reading or score. The exporter reads a list's language and moments, and
nothing of its entries, counts or parameters. The reader takes the new
tables through its default privileges.
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

# revision identifiers, used by Alembic.
revision: str = "549774d4e09c"
down_revision: str | Sequence[str] | None = "5e7cef4aa5d0"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

BOT = "itgraph_bot"
METRICS = "itgraph_metrics"

# A list's delivery state and an entry's, and nothing either was computed
# with.
BOT_UPDATES = {
    "yt_weekly_lists": ("delivered_at", "attempts", "last_error"),
    "yt_weekly_entries": ("delivered_at",),
}
# What the series need of a list.
METRICS_READS = ("route", "computed_at", "delivered_at")


def _type(name: str) -> postgresql.ENUM:
    return postgresql.ENUM(name=name, create_type=False)


def upgrade() -> None:
    """Upgrade schema."""
    op.create_table(
        "yt_weekly_lists",
        sa.Column("id", sa.BigInteger(), nullable=False),
        sa.Column("week_start", sa.Date(), nullable=False),
        sa.Column("route", _type("yt_alert_route"), nullable=False),
        sa.Column("computed_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("threshold", sa.Float(), nullable=False),
        sa.Column("size", sa.Integer(), nullable=False),
        sa.Column("comments_min", sa.Integer(), nullable=False),
        sa.Column("videos_scored", sa.Integer(), nullable=False),
        sa.Column("channels_above", sa.Integer(), nullable=False),
        sa.Column("delivered_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column(
            "attempts",
            sa.Integer(),
            server_default=sa.text("0"),
            nullable=False,
        ),
        sa.Column("last_error", sa.Text(), nullable=True),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_yt_weekly_lists")),
        sa.UniqueConstraint(
            "week_start", "route", name="uq_yt_weekly_lists_week"
        ),
    )
    op.create_index(
        "ix_yt_weekly_lists_undelivered",
        "yt_weekly_lists",
        ["week_start"],
        unique=False,
        postgresql_where=sa.text("delivered_at IS NULL"),
    )
    op.create_table(
        "yt_weekly_entries",
        sa.Column("list_id", sa.BigInteger(), nullable=False),
        sa.Column("rank", sa.Integer(), nullable=False),
        sa.Column("channel_id", sa.Text(), nullable=False),
        sa.Column("video_id", sa.Text(), nullable=False),
        sa.Column("scored_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("metric", _type("yt_metric"), nullable=False),
        sa.Column("value", sa.Float(), nullable=False),
        sa.Column("expected", sa.Float(), nullable=False),
        sa.Column("others", sa.Integer(), nullable=False),
        sa.Column("delivered_at", sa.DateTime(timezone=True), nullable=True),
        sa.ForeignKeyConstraint(
            ["channel_id"],
            ["yt_channels.channel_id"],
            name=op.f("fk_yt_weekly_entries_channel_id_yt_channels"),
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["list_id"],
            ["yt_weekly_lists.id"],
            name=op.f("fk_yt_weekly_entries_list_id_yt_weekly_lists"),
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["video_id", "scored_at"],
            ["yt_raw_video_stats.video_id", "yt_raw_video_stats.observed_at"],
            name=op.f("fk_yt_weekly_entries_video_id_yt_raw_video_stats"),
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint(
            "list_id", "rank", name=op.f("pk_yt_weekly_entries")
        ),
        sa.UniqueConstraint(
            "list_id", "channel_id", name="uq_yt_weekly_entries_channel"
        ),
    )

    for table, columns in BOT_UPDATES.items():
        op.execute(f"GRANT SELECT ON {table} TO {BOT}")
        op.execute(f"GRANT UPDATE ({', '.join(columns)}) ON {table} TO {BOT}")
    op.execute(
        f"GRANT SELECT ({', '.join(METRICS_READS)}) ON yt_weekly_lists "
        f"TO {METRICS}"
    )


def downgrade() -> None:
    """Downgrade schema.

    The grants go with the tables.
    """
    op.drop_table("yt_weekly_entries")
    op.drop_index(
        "ix_yt_weekly_lists_undelivered",
        table_name="yt_weekly_lists",
        postgresql_where=sa.text("delivered_at IS NULL"),
    )
    op.drop_table("yt_weekly_lists")
