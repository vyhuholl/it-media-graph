"""raw layer

Revision ID: a844cb935a57
Revises: 286ba7c9b71e
Create Date: 2026-07-22 12:26:02.468569

"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

# revision identifiers, used by Alembic.
revision: str = "a844cb935a57"
down_revision: str | Sequence[str] | None = "286ba7c9b71e"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

# Created implicitly by the columns that use them, dropped explicitly:
# `drop_table` leaves enum types behind, and the next `upgrade` would
# then fail on "type already exists".
ENUM_TYPES = ("backfill_status", "failure_kind")


def upgrade() -> None:
    """Upgrade schema."""
    op.create_table(
        "backfill_state",
        sa.Column(
            "channel_id", sa.BigInteger(), autoincrement=False, nullable=False
        ),
        sa.Column("oldest_fetched_id", sa.BigInteger(), nullable=True),
        sa.Column("newest_fetched_id", sa.BigInteger(), nullable=True),
        sa.Column("cutoff_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column(
            "status",
            sa.Enum(
                "pending",
                "running",
                "complete",
                "skipped",
                "failed",
                name="backfill_status",
            ),
            server_default="pending",
            nullable=False,
        ),
        sa.Column(
            "failure_kind",
            sa.Enum("permanent", "transient", name="failure_kind"),
            nullable=True,
        ),
        sa.Column("failure_detail", sa.Text(), nullable=True),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.ForeignKeyConstraint(
            ["channel_id"],
            ["channels.tg_id"],
            name=op.f("fk_backfill_state_channel_id_channels"),
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("channel_id", name=op.f("pk_backfill_state")),
    )
    op.create_table(
        "raw_channels",
        sa.Column(
            "channel_id", sa.BigInteger(), autoincrement=False, nullable=False
        ),
        sa.Column(
            "payload",
            postgresql.JSONB(astext_type=sa.Text()),
            nullable=False,
        ),
        sa.Column(
            "fetched_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.ForeignKeyConstraint(
            ["channel_id"],
            ["channels.tg_id"],
            name=op.f("fk_raw_channels_channel_id_channels"),
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("channel_id", name=op.f("pk_raw_channels")),
    )
    op.create_table(
        "raw_messages",
        sa.Column(
            "channel_id", sa.BigInteger(), autoincrement=False, nullable=False
        ),
        sa.Column(
            "msg_id", sa.BigInteger(), autoincrement=False, nullable=False
        ),
        sa.Column(
            "payload",
            postgresql.JSONB(astext_type=sa.Text()),
            nullable=False,
        ),
        sa.Column(
            "fetched_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.ForeignKeyConstraint(
            ["channel_id"],
            ["channels.tg_id"],
            name=op.f("fk_raw_messages_channel_id_channels"),
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint(
            "channel_id", "msg_id", name=op.f("pk_raw_messages")
        ),
    )
    op.add_column(
        "channels",
        sa.Column("last_post_at", sa.DateTime(timezone=True), nullable=True),
    )


def downgrade() -> None:
    """Downgrade schema."""
    op.drop_column("channels", "last_post_at")
    op.drop_table("raw_messages")
    op.drop_table("raw_channels")
    op.drop_table("backfill_state")
    for name in ENUM_TYPES:
        op.execute(f"DROP TYPE IF EXISTS {name}")
