"""metric snapshots and poll state

Revision ID: 67382317442f
Revises: 9231985c78b0
Create Date: 2026-08-03 18:14:11.404309

The two tables the watch loop writes. `message_metrics` is the raw layer
for counters that move, which `raw_messages` cannot be: that table is
immutable and first-fetch-wins, which is right for a message body and
useless for a view count. `poll_state` is when each channel is next due —
timing only, since the position stays on `backfill_state.newest_fetched_id`.

Both are additive. Nothing already stored changes shape and no existing
row is rewritten, so the downgrade is a clean drop.

"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

# revision identifiers, used by Alembic.
revision: str = "67382317442f"
down_revision: str | Sequence[str] | None = "9231985c78b0"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """Upgrade schema."""
    op.create_table(
        "poll_state",
        sa.Column(
            "channel_id", sa.BigInteger(), autoincrement=False, nullable=False
        ),
        sa.Column("due_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("last_polled_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("posts_per_day", sa.Float(), nullable=True),
        sa.Column(
            "posts_per_day_at", sa.DateTime(timezone=True), nullable=True
        ),
        sa.Column(
            "consecutive_empty",
            sa.Integer(),
            server_default=sa.text("0"),
            nullable=False,
        ),
        sa.Column(
            "consecutive_failures",
            sa.Integer(),
            server_default=sa.text("0"),
            nullable=False,
        ),
        sa.Column("last_error", sa.Text(), nullable=True),
        sa.ForeignKeyConstraint(
            ["channel_id"],
            ["channels.tg_id"],
            name=op.f("fk_poll_state_channel_id_channels"),
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("channel_id", name=op.f("pk_poll_state")),
    )
    op.create_index(
        op.f("ix_poll_state_due_at"), "poll_state", ["due_at"], unique=False
    )

    # The foreign key is onto `raw_messages`, not `channels`: a snapshot
    # of a message the raw layer does not hold must be impossible, and
    # that is what pins the write order to payload-then-snapshot. It
    # references that table's primary key, so it needs no index of its
    # own.
    op.create_table(
        "message_metrics",
        sa.Column(
            "channel_id", sa.BigInteger(), autoincrement=False, nullable=False
        ),
        sa.Column(
            "msg_id", sa.BigInteger(), autoincrement=False, nullable=False
        ),
        sa.Column("observed_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("views", sa.Integer(), nullable=True),
        sa.Column("forwards", sa.Integer(), nullable=True),
        sa.Column(
            "reactions",
            postgresql.JSONB(astext_type=sa.Text()),
            nullable=True,
        ),
        sa.Column("comments", sa.Integer(), nullable=True),
        sa.ForeignKeyConstraint(
            ["channel_id", "msg_id"],
            ["raw_messages.channel_id", "raw_messages.msg_id"],
            name=op.f("fk_message_metrics_channel_id_raw_messages"),
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint(
            "channel_id",
            "msg_id",
            "observed_at",
            name=op.f("pk_message_metrics"),
        ),
    )
    # Not a prefix of the primary key, which leads with the channel: the
    # alert pass reads "every snapshot since I last ran".
    op.create_index(
        "ix_message_metrics_observed_at",
        "message_metrics",
        ["observed_at"],
        unique=False,
    )


def downgrade() -> None:
    """Downgrade schema.

    A clean drop: nothing outside these two tables was touched on the way
    up. The `watch` enum value added by the following revision is not
    removed here — see that revision for why it cannot be.
    """
    op.drop_index(
        "ix_message_metrics_observed_at", table_name="message_metrics"
    )
    op.drop_table("message_metrics")
    op.drop_index(op.f("ix_poll_state_due_at"), table_name="poll_state")
    op.drop_table("poll_state")
