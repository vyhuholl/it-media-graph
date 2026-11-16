"""second wind

Revision ID: b092acf2a3ff
Revises: 1156f1425a66
Create Date: 2026-11-16 16:18:41.240358

The second wind section of both platforms' weekly lists: an older post or
video whose views sped up this week, one row per channel and list.

**A row goes with its readings**: Telegram's with the one that ends its
week, YouTube's with the ones that start and end it, by foreign keys with
`ON DELETE CASCADE`. YouTube's copies nothing YouTube said — moments and
itgraph's multiples only.

**The lists record the shadow**: whether the section was switched on when
the list was computed, which is when the bot sends it, and the thresholds
it was computed with.

**The grants.** The bot reads both tables and may change only a row's
delivery state. The exporter reads nothing of them. The reader takes them
through its default privileges.
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision: str = "b092acf2a3ff"
down_revision: str | Sequence[str] | None = "1156f1425a66"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

BOT = "itgraph_bot"
TABLES = ("weekly_revivals", "yt_weekly_revivals")
LISTS = ("weekly_lists", "yt_weekly_lists")


def upgrade() -> None:
    """Upgrade schema."""
    op.create_table(
        "yt_weekly_revivals",
        sa.Column("list_id", sa.BigInteger(), nullable=False),
        sa.Column("rank", sa.Integer(), nullable=False),
        sa.Column("channel_id", sa.Text(), nullable=False),
        sa.Column("video_id", sa.Text(), nullable=False),
        sa.Column("before_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("start_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("end_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("acceleration", sa.Float(), nullable=True),
        sa.Column("share", sa.Float(), nullable=False),
        sa.Column("others", sa.Integer(), nullable=False),
        sa.Column("delivered_at", sa.DateTime(timezone=True), nullable=True),
        sa.ForeignKeyConstraint(
            ["channel_id"],
            ["yt_channels.channel_id"],
            name=op.f("fk_yt_weekly_revivals_channel_id_yt_channels"),
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["list_id"],
            ["yt_weekly_lists.id"],
            name=op.f("fk_yt_weekly_revivals_list_id_yt_weekly_lists"),
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["video_id", "end_at"],
            ["yt_raw_video_stats.video_id", "yt_raw_video_stats.observed_at"],
            name="fk_yt_weekly_revivals_end",
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["video_id", "start_at"],
            ["yt_raw_video_stats.video_id", "yt_raw_video_stats.observed_at"],
            name="fk_yt_weekly_revivals_start",
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint(
            "list_id", "rank", name=op.f("pk_yt_weekly_revivals")
        ),
        sa.UniqueConstraint(
            "list_id", "channel_id", name="uq_yt_weekly_revivals_channel"
        ),
    )
    op.create_table(
        "weekly_revivals",
        sa.Column("list_id", sa.BigInteger(), nullable=False),
        sa.Column("rank", sa.Integer(), nullable=False),
        sa.Column("channel_id", sa.BigInteger(), nullable=False),
        sa.Column("msg_id", sa.BigInteger(), nullable=False),
        sa.Column("before_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("start_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("end_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("previous_pace", sa.Float(), nullable=True),
        sa.Column("pace", sa.Float(), nullable=False),
        sa.Column("norm", sa.Float(), nullable=False),
        sa.Column("others", sa.Integer(), nullable=False),
        sa.Column("delivered_at", sa.DateTime(timezone=True), nullable=True),
        sa.ForeignKeyConstraint(
            ["channel_id", "msg_id", "end_at"],
            [
                "message_metrics.channel_id",
                "message_metrics.msg_id",
                "message_metrics.observed_at",
            ],
            name=op.f("fk_weekly_revivals_channel_id_message_metrics"),
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["channel_id"],
            ["channels.tg_id"],
            name=op.f("fk_weekly_revivals_channel_id_channels"),
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["list_id"],
            ["weekly_lists.id"],
            name=op.f("fk_weekly_revivals_list_id_weekly_lists"),
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint(
            "list_id", "rank", name=op.f("pk_weekly_revivals")
        ),
        sa.UniqueConstraint(
            "list_id", "channel_id", name="uq_weekly_revivals_channel"
        ),
    )
    for table in LISTS:
        op.add_column(
            table,
            sa.Column(
                "second_wind_shown",
                sa.Boolean(),
                server_default=sa.text("false"),
                nullable=False,
            ),
        )
        op.add_column(
            table,
            sa.Column("second_wind_acceleration", sa.Float(), nullable=True),
        )
        op.add_column(
            table,
            sa.Column("second_wind_norm_share", sa.Float(), nullable=True),
        )
        op.add_column(
            table, sa.Column("second_wind_size", sa.Integer(), nullable=True)
        )

    for table in TABLES:
        op.execute(f"GRANT SELECT ON {table} TO {BOT}")
        op.execute(f"GRANT UPDATE (delivered_at) ON {table} TO {BOT}")


def downgrade() -> None:
    """Downgrade schema.

    The grants go with the tables.
    """
    for table in LISTS:
        op.drop_column(table, "second_wind_size")
        op.drop_column(table, "second_wind_norm_share")
        op.drop_column(table, "second_wind_acceleration")
        op.drop_column(table, "second_wind_shown")
    op.drop_table("weekly_revivals")
    op.drop_table("yt_weekly_revivals")
