"""telegram weekly lists

Revision ID: 1156f1425a66
Revises: 065cd92181b4
Create Date: 2026-11-16 10:57:50.788556

Telegram's list of what took off in a week: a list per week, and an entry
per channel naming its best post. The existing `metric` type is reused.

**An entry lives as long as the reading its score came from**, by a
foreign key with `ON DELETE CASCADE` to `message_metrics`. The list row
holds no post and stays.

**The grants.** The bot reads both tables and may change only their
delivery state: not write a list, and not touch an entry's rank, post,
reading, signal, count or score. The exporter reads a list's moments,
and nothing of its week, counts, parameters or entries. The reader takes
the new tables through its default privileges.
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

# revision identifiers, used by Alembic.
revision: str = "1156f1425a66"
down_revision: str | Sequence[str] | None = "065cd92181b4"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

BOT = "itgraph_bot"
METRICS = "itgraph_metrics"

# A list's delivery state and an entry's, and nothing either was computed
# with.
BOT_UPDATES = {
    "weekly_lists": ("delivered_at", "attempts", "last_error"),
    "weekly_entries": ("delivered_at",),
}
# What the series need of a list.
METRICS_READS = ("computed_at", "delivered_at")


def upgrade() -> None:
    """Upgrade schema."""
    op.create_table(
        "weekly_lists",
        sa.Column("id", sa.BigInteger(), nullable=False),
        sa.Column("week_start", sa.Date(), nullable=False),
        sa.Column("computed_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("threshold", sa.Float(), nullable=False),
        sa.Column("size", sa.Integer(), nullable=False),
        sa.Column("comments_min", sa.Integer(), nullable=False),
        sa.Column("posts_scored", sa.Integer(), nullable=False),
        sa.Column("channels_above", sa.Integer(), nullable=False),
        sa.Column("delivered_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column(
            "attempts",
            sa.Integer(),
            server_default=sa.text("0"),
            nullable=False,
        ),
        sa.Column("last_error", sa.Text(), nullable=True),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_weekly_lists")),
        sa.UniqueConstraint("week_start", name="uq_weekly_lists_week"),
    )
    op.create_index(
        "ix_weekly_lists_undelivered",
        "weekly_lists",
        ["week_start"],
        unique=False,
        postgresql_where=sa.text("delivered_at IS NULL"),
    )
    op.create_table(
        "weekly_entries",
        sa.Column("list_id", sa.BigInteger(), nullable=False),
        sa.Column("rank", sa.Integer(), nullable=False),
        sa.Column("channel_id", sa.BigInteger(), nullable=False),
        sa.Column("msg_id", sa.BigInteger(), nullable=False),
        sa.Column("scored_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column(
            "metric",
            postgresql.ENUM(name="metric", create_type=False),
            nullable=False,
        ),
        sa.Column("value", sa.Float(), nullable=False),
        sa.Column("observed", sa.Float(), nullable=False),
        sa.Column("expected", sa.Float(), nullable=False),
        sa.Column("others", sa.Integer(), nullable=False),
        sa.Column("delivered_at", sa.DateTime(timezone=True), nullable=True),
        sa.ForeignKeyConstraint(
            ["channel_id", "msg_id", "scored_at"],
            [
                "message_metrics.channel_id",
                "message_metrics.msg_id",
                "message_metrics.observed_at",
            ],
            name=op.f("fk_weekly_entries_channel_id_message_metrics"),
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["channel_id"],
            ["channels.tg_id"],
            name=op.f("fk_weekly_entries_channel_id_channels"),
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["list_id"],
            ["weekly_lists.id"],
            name=op.f("fk_weekly_entries_list_id_weekly_lists"),
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint(
            "list_id", "rank", name=op.f("pk_weekly_entries")
        ),
        sa.UniqueConstraint(
            "list_id", "channel_id", name="uq_weekly_entries_channel"
        ),
    )

    for table, columns in BOT_UPDATES.items():
        op.execute(f"GRANT SELECT ON {table} TO {BOT}")
        op.execute(f"GRANT UPDATE ({', '.join(columns)}) ON {table} TO {BOT}")
    op.execute(
        f"GRANT SELECT ({', '.join(METRICS_READS)}) ON weekly_lists "
        f"TO {METRICS}"
    )


def downgrade() -> None:
    """Downgrade schema.

    The grants go with the tables.
    """
    op.drop_table("weekly_entries")
    op.drop_index(
        "ix_weekly_lists_undelivered",
        table_name="weekly_lists",
        postgresql_where=sa.text("delivered_at IS NULL"),
    )
    op.drop_table("weekly_lists")
