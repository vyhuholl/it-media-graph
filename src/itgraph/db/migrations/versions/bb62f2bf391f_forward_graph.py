"""forward graph

Revision ID: bb62f2bf391f
Revises: a844cb935a57
Create Date: 2026-07-24 08:23:38.824939

"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision: str = "bb62f2bf391f"
down_revision: str | Sequence[str] | None = "a844cb935a57"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

# Created implicitly by the column that uses it, dropped explicitly:
# `drop_table` leaves the enum type behind, and the next `upgrade` would
# then fail on "type already exists".
ENUM_TYPES = ("edge_kind",)


def upgrade() -> None:
    """Upgrade schema."""
    op.create_table(
        "pending_mentions",
        sa.Column("username", sa.Text(), nullable=False),
        sa.Column(
            "first_seen_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column(
            "attempts",
            sa.Integer(),
            server_default=sa.text("0"),
            nullable=False,
        ),
        sa.Column(
            "last_attempt_at", sa.DateTime(timezone=True), nullable=True
        ),
        sa.Column("last_error", sa.Text(), nullable=True),
        sa.PrimaryKeyConstraint("username", name=op.f("pk_pending_mentions")),
    )
    op.create_table(
        "edges",
        sa.Column("id", sa.BigInteger(), nullable=False),
        sa.Column("src_channel_id", sa.BigInteger(), nullable=False),
        sa.Column("dst_channel_id", sa.BigInteger(), nullable=False),
        sa.Column(
            "kind",
            sa.Enum("forward", "mention", name="edge_kind"),
            nullable=False,
        ),
        sa.Column("msg_id", sa.BigInteger(), nullable=False),
        sa.Column("published_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column(
            "derived_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.ForeignKeyConstraint(
            ["dst_channel_id"],
            ["channels.tg_id"],
            name=op.f("fk_edges_dst_channel_id_channels"),
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["src_channel_id"],
            ["channels.tg_id"],
            name=op.f("fk_edges_src_channel_id_channels"),
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_edges")),
        sa.UniqueConstraint(
            "src_channel_id",
            "msg_id",
            "kind",
            "dst_channel_id",
            name="observed_reference",
        ),
    )
    op.create_index(
        op.f("ix_edges_dst_channel_id"),
        "edges",
        ["dst_channel_id"],
        unique=False,
    )
    op.create_index(
        op.f("ix_edges_published_at"),
        "edges",
        ["published_at"],
        unique=False,
    )
    op.create_index(
        op.f("ix_edges_src_channel_id"),
        "edges",
        ["src_channel_id"],
        unique=False,
    )
    op.add_column(
        "channels",
        sa.Column("resolved_at", sa.DateTime(timezone=True), nullable=True),
    )
    op.add_column(
        "channels",
        sa.Column(
            "resolve_attempts",
            sa.Integer(),
            server_default=sa.text("0"),
            nullable=False,
        ),
    )
    op.add_column(
        "channels",
        sa.Column(
            "resolve_last_attempt_at",
            sa.DateTime(timezone=True),
            nullable=True,
        ),
    )
    op.add_column(
        "channels", sa.Column("resolve_last_error", sa.Text(), nullable=True)
    )

    # Every row that exists before this migration came from the dialog
    # import or the metadata pass, and so already carries a username and
    # title. Stamp them resolved from their first-seen time, so the
    # resolve pass — which queues on `resolved_at IS NULL` — does not go
    # asking Telegram to re-learn identities the inventory already holds.
    op.execute(
        "UPDATE channels SET resolved_at = first_seen_at "
        "WHERE username IS NOT NULL"
    )


def downgrade() -> None:
    """Downgrade schema."""
    op.drop_column("channels", "resolve_last_error")
    op.drop_column("channels", "resolve_last_attempt_at")
    op.drop_column("channels", "resolve_attempts")
    op.drop_column("channels", "resolved_at")
    op.drop_index(op.f("ix_edges_src_channel_id"), table_name="edges")
    op.drop_index(op.f("ix_edges_published_at"), table_name="edges")
    op.drop_index(op.f("ix_edges_dst_channel_id"), table_name="edges")
    op.drop_table("edges")
    op.drop_table("pending_mentions")
    for name in ENUM_TYPES:
        op.execute(f"DROP TYPE IF EXISTS {name}")
