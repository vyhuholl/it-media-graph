"""flood events

Revision ID: 6cd4607f7d9b
Revises: cc62e4abcd69
Create Date: 2026-07-26 09:56:46.920401

"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision: str = "6cd4607f7d9b"
down_revision: str | Sequence[str] | None = "cc62e4abcd69"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

# Dropping the table leaves the type behind, and the next `upgrade` then
# fails with "type already exists". Autogenerate does not emit these.
ENUM_TYPES = ("collection_command",)


def upgrade() -> None:
    """Upgrade schema."""
    op.create_table(
        "flood_events",
        sa.Column("id", sa.BigInteger(), autoincrement=True, nullable=False),
        sa.Column(
            "occurred_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column("method", sa.Text(), nullable=False),
        sa.Column("seconds", sa.Integer(), nullable=False),
        sa.Column(
            "command",
            sa.Enum("backfill", "resolve", name="collection_command"),
            nullable=False,
        ),
        sa.Column("channel_id", sa.BigInteger(), nullable=True),
        sa.Column(
            "halted",
            sa.Boolean(),
            server_default=sa.text("false"),
            nullable=False,
        ),
        sa.ForeignKeyConstraint(
            ["channel_id"],
            ["channels.tg_id"],
            name=op.f("fk_flood_events_channel_id_channels"),
            ondelete="SET NULL",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_flood_events")),
    )
    op.create_index(
        "ix_flood_events_method", "flood_events", ["method"], unique=False
    )
    op.create_index(
        "ix_flood_events_occurred_at",
        "flood_events",
        ["occurred_at"],
        unique=False,
    )


def downgrade() -> None:
    """Downgrade schema."""
    op.drop_index("ix_flood_events_occurred_at", table_name="flood_events")
    op.drop_index("ix_flood_events_method", table_name="flood_events")
    op.drop_table("flood_events")
    for name in ENUM_TYPES:
        op.execute(f"DROP TYPE IF EXISTS {name}")
