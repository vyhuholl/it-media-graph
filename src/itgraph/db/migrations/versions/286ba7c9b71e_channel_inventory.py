"""channel inventory

Revision ID: 286ba7c9b71e
Revises:
Create Date: 2026-07-21 08:42:34.992602

"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

# revision identifiers, used by Alembic.
revision: str = "286ba7c9b71e"
down_revision: str | Sequence[str] | None = None
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

# Created implicitly by the columns that use them, dropped explicitly:
# `drop_table` leaves enum types behind, and the next `upgrade` would
# then fail on "type already exists".
ENUM_TYPES = (
    "channel_status",
    "channel_kind",
    "reject_reason",
    "discovery_source",
)


def upgrade() -> None:
    """Upgrade schema."""
    op.create_table(
        "channels",
        sa.Column(
            "tg_id", sa.BigInteger(), autoincrement=False, nullable=False
        ),
        sa.Column("username", sa.Text(), nullable=True),
        sa.Column("title", sa.Text(), nullable=True),
        sa.Column(
            "is_chat",
            sa.Boolean(),
            server_default=sa.text("false"),
            nullable=False,
        ),
        sa.Column(
            "status",
            sa.Enum(
                "candidate",
                "seed",
                "maybe",
                "rejected",
                name="channel_status",
            ),
            server_default="candidate",
            nullable=False,
        ),
        sa.Column(
            "reject_reason",
            sa.Enum(
                "not_it",
                "adjacent",
                "crypto",
                "infobiz",
                "ads",
                "content_farm",
                "other_scene",
                name="reject_reason",
            ),
            nullable=True,
        ),
        sa.Column("reject_note", sa.Text(), nullable=True),
        sa.Column(
            "kind",
            sa.Enum(
                "personal",
                "aggregator",
                "company",
                "vacancies",
                "media",
                "community",
                "event",
                name="channel_kind",
            ),
            nullable=True,
        ),
        sa.Column("kind_note", sa.Text(), nullable=True),
        sa.Column(
            "discovered_via",
            sa.Enum(
                "own_subscriptions",
                "forward",
                "recommendation",
                "mention",
                "manual",
                "linked_chat",
                name="discovery_source",
            ),
            nullable=False,
        ),
        sa.Column(
            "first_seen_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column("reviewed_at", sa.DateTime(timezone=True), nullable=True),
        sa.CheckConstraint(
            "(status = 'rejected') = (reject_reason IS NOT NULL)",
            name=op.f("ck_channels_rejected_has_reason"),
        ),
        sa.PrimaryKeyConstraint("tg_id", name=op.f("pk_channels")),
    )

    op.add_column(
        "channels", sa.Column("linked_to", sa.BigInteger(), nullable=True)
    )
    op.create_foreign_key(
        "fk_channels_linked_to_channels",
        "channels",
        "channels",
        ["linked_to"],
        ["tg_id"],
        ondelete="SET NULL",
    )


def downgrade() -> None:
    """Downgrade schema."""
    op.drop_table("channels")
    for name in ENUM_TYPES:
        op.execute(f"DROP TYPE IF EXISTS {name}")
