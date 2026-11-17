"""rising offers

Revision ID: c060371f685d
Revises: b13d2bd31424
Create Date: 2026-11-17 16:03:49.400701

Telegram's rising channels offered for review in the bot.

**A day and its offers.** `tg_rising_offer_days` holds one row per day the
selection ran, an empty one too, so the series can tell "nothing rose"
from "the pass did not run". `tg_rising_offers` holds each channel
offered — once, ever: `channel_id` is unique — with the ranking's numbers
as they stood, the bot's delivery, the decision pressed and whether the
detection pass could apply it. No name, no title and nobody who pressed.

**The grants.** The bot reads both and writes only an offer's delivery
and its decision: no new privilege on the inventory, which the detection
pass changes under its own role. The exporter reads the moments alone —
when a day was computed, and when an offer was offered, delivered,
decided and applied — and neither which channel nor what was decided.
The reader takes both through its default privileges. The restore closes
what would act on the copy (`files/neutralise.sql`).
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

# revision identifiers, used by Alembic.
revision: str = "c060371f685d"
down_revision: str | Sequence[str] | None = "b13d2bd31424"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

BOT = "itgraph_bot"
METRICS = "itgraph_metrics"
# What the bot may write of an offer: its delivery and its decision.
BOT_UPDATES = (
    "delivered_at",
    "attempts",
    "last_error",
    "decision",
    "kind",
    "reject_reason",
    "decided_at",
)
# What the series need: moments, and nothing of what was offered.
METRICS_READS = {
    "tg_rising_offer_days": ("computed_at",),
    "tg_rising_offers": (
        "offered_at",
        "delivered_at",
        "decided_at",
        "applied_at",
    ),
}


def upgrade() -> None:
    """Upgrade schema."""
    op.create_table(
        "tg_rising_offer_days",
        sa.Column("day", sa.Date(), nullable=False),
        sa.Column("computed_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("size", sa.Integer(), nullable=False),
        sa.PrimaryKeyConstraint("day", name=op.f("pk_tg_rising_offer_days")),
    )
    op.create_table(
        "tg_rising_offers",
        sa.Column("id", sa.BigInteger(), nullable=False),
        sa.Column("day", sa.Date(), nullable=False),
        sa.Column("channel_id", sa.BigInteger(), nullable=False),
        sa.Column("rank", sa.Integer(), nullable=False),
        sa.Column("families", sa.Integer(), nullable=False),
        sa.Column("families_before", sa.Integer(), nullable=False),
        sa.Column(
            "first_in_window", sa.DateTime(timezone=True), nullable=True
        ),
        sa.Column("offered_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("delivered_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column(
            "attempts", sa.Integer(), server_default="0", nullable=False
        ),
        sa.Column("last_error", sa.Text(), nullable=True),
        sa.Column(
            "decision",
            postgresql.ENUM(name="channel_status", create_type=False),
            nullable=True,
        ),
        sa.Column(
            "kind",
            postgresql.ENUM(name="tg_channel_kind", create_type=False),
            nullable=True,
        ),
        sa.Column(
            "reject_reason",
            postgresql.ENUM(name="tg_reject_reason", create_type=False),
            nullable=True,
        ),
        sa.Column("decided_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("applied_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("applied", sa.Boolean(), nullable=True),
        sa.CheckConstraint(
            "coalesce(decision = 'seed', false) = (kind IS NOT NULL)"
            " AND coalesce(decision = 'rejected', false)"
            " = (reject_reason IS NOT NULL)",
            name=op.f("ck_tg_rising_offers_decision_has_its_choice"),
        ),
        sa.CheckConstraint(
            "decision IS NULL OR decision <> 'candidate'",
            name=op.f("ck_tg_rising_offers_decision_is_a_review"),
        ),
        sa.CheckConstraint(
            "(applied_at IS NULL) = (applied IS NULL)"
            " AND (applied_at IS NULL OR decided_at IS NOT NULL)",
            name=op.f("ck_tg_rising_offers_applied_after_decided"),
        ),
        sa.CheckConstraint(
            "(decision IS NULL) = (decided_at IS NULL)",
            name=op.f("ck_tg_rising_offers_decided_when_decided"),
        ),
        sa.ForeignKeyConstraint(
            ["channel_id"],
            ["tg_channels.tg_id"],
            name=op.f("fk_tg_rising_offers_channel_id_tg_channels"),
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["day"],
            ["tg_rising_offer_days.day"],
            name=op.f("fk_tg_rising_offers_day_tg_rising_offer_days"),
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_tg_rising_offers")),
        sa.UniqueConstraint("channel_id", name="uq_tg_rising_offers_channel"),
    )
    op.create_index(
        "ix_tg_rising_offers_unapplied",
        "tg_rising_offers",
        ["decided_at"],
        unique=False,
        postgresql_where=sa.text(
            "decided_at IS NOT NULL AND applied_at IS NULL"
        ),
    )
    op.create_index(
        "ix_tg_rising_offers_undelivered",
        "tg_rising_offers",
        ["day", "rank"],
        unique=False,
        postgresql_where=sa.text("delivered_at IS NULL"),
    )

    for table in METRICS_READS:
        op.execute(f"GRANT SELECT ON {table} TO {BOT}")
    op.execute(
        f"GRANT UPDATE ({', '.join(BOT_UPDATES)}) ON tg_rising_offers TO {BOT}"
    )
    for table, columns in METRICS_READS.items():
        op.execute(
            f"GRANT SELECT ({', '.join(columns)}) ON {table} TO {METRICS}"
        )


def downgrade() -> None:
    """Downgrade schema.

    The grants go with the tables.
    """
    op.drop_index(
        "ix_tg_rising_offers_undelivered", table_name="tg_rising_offers"
    )
    op.drop_index(
        "ix_tg_rising_offers_unapplied", table_name="tg_rising_offers"
    )
    op.drop_table("tg_rising_offers")
    op.drop_table("tg_rising_offer_days")
