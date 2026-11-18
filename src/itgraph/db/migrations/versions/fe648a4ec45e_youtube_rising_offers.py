"""youtube rising offers

Revision ID: fe648a4ec45e
Revises: a6cb6bd0dfbc
Create Date: 2026-11-19 10:12:31.207418

YouTube's rising channels offered for review in the bot.

**A day and its offers.** `yt_rising_offer_days` holds one row per day
the selection ran, an empty one too, with the two sizes it was computed
with. `yt_rising_offers` holds each channel offered — once, ever:
`channel_id` is unique — with whichever of the two rankings offered it
and what it filled, the discovery source of the record the selection
created, the topic its card goes to, the bot's delivery, the decision
pressed and whether the detection pass could apply it. No title, no
handle and nobody who pressed; the bot reads the channel at sending.

**The enums.** `yt_rising_ranking` is new — `link` or `chart`. The
discovery source set gains `chart`, which no command writes by hand:
the day's selection alone does. Neither is used by this revision's own
DDL, so one transaction serves.

**The grants.** Telegram's, verbatim in shape: the bot reads both and
writes only an offer's delivery and its decision — a language included,
as `itgraph yt mark` records one; the exporter reads the moments alone;
the reader takes both through its default privileges. The bot also
takes the chart readings whole: a chart offer's card names the video
that charted it, read from the stored reading at sending, and the
payloads the bot renders from are already its to read. The restore
closes what would act on the copy (`files/neutralise.sql`).
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

# revision identifiers, used by Alembic.
revision: str = "fe648a4ec45e"
down_revision: str | Sequence[str] | None = "a6cb6bd0dfbc"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

BOT = "itgraph_bot"
METRICS = "itgraph_metrics"
RANKING = postgresql.ENUM("link", "chart", name="yt_rising_ranking")
# What the bot may write of an offer: its delivery and its decision.
BOT_UPDATES = (
    "delivered_at",
    "attempts",
    "last_error",
    "decision",
    "kind",
    "language",
    "reject_reason",
    "decided_at",
)
# What the series need: moments, and nothing of what was offered.
METRICS_READS = {
    "yt_rising_offer_days": ("computed_at",),
    "yt_rising_offers": (
        "offered_at",
        "delivered_at",
        "decided_at",
        "applied_at",
    ),
}


def upgrade() -> None:
    """Upgrade schema."""
    bind = op.get_bind()
    RANKING.create(bind, checkfirst=True)
    op.execute(
        "ALTER TYPE yt_discovery_source ADD VALUE IF NOT EXISTS 'chart'"
    )
    op.create_table(
        "yt_rising_offer_days",
        sa.Column("day", sa.Date(), nullable=False),
        sa.Column("computed_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("size_chart", sa.Integer(), nullable=False),
        sa.Column("size_links", sa.Integer(), nullable=False),
        sa.PrimaryKeyConstraint("day", name=op.f("pk_yt_rising_offer_days")),
    )
    op.create_table(
        "yt_rising_offers",
        sa.Column("id", sa.BigInteger(), nullable=False),
        sa.Column("day", sa.Date(), nullable=False),
        sa.Column("channel_id", sa.Text(), nullable=False),
        sa.Column(
            "ranking",
            postgresql.ENUM(name="yt_rising_ranking", create_type=False),
            nullable=False,
        ),
        sa.Column("region", sa.Text(), nullable=True),
        sa.Column("position", sa.Integer(), nullable=True),
        sa.Column("families", sa.Integer(), nullable=True),
        sa.Column("families_before", sa.Integer(), nullable=True),
        sa.Column(
            "first_in_window", sa.DateTime(timezone=True), nullable=True
        ),
        sa.Column(
            "discovered_via",
            postgresql.ENUM(name="yt_discovery_source", create_type=False),
            nullable=False,
        ),
        sa.Column(
            "topic",
            postgresql.ENUM(name="yt_alert_route", create_type=False),
            nullable=False,
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
            postgresql.ENUM(name="yt_channel_kind", create_type=False),
            nullable=True,
        ),
        sa.Column(
            "language",
            postgresql.ENUM(name="yt_lang", create_type=False),
            nullable=True,
        ),
        sa.Column(
            "reject_reason",
            postgresql.ENUM(name="yt_reject_reason", create_type=False),
            nullable=True,
        ),
        sa.Column("decided_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("applied_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("applied", sa.Boolean(), nullable=True),
        sa.CheckConstraint(
            "coalesce(decision = 'seed', false) = (kind IS NOT NULL)"
            " AND coalesce(decision = 'seed', false)"
            " = (language IS NOT NULL)"
            " AND coalesce(decision = 'rejected', false)"
            " = (reject_reason IS NOT NULL)",
            name=op.f("ck_yt_rising_offers_decision_has_its_choice"),
        ),
        sa.CheckConstraint(
            "decision IS NULL OR decision <> 'candidate'",
            name=op.f("ck_yt_rising_offers_decision_is_a_review"),
        ),
        sa.CheckConstraint(
            "(applied_at IS NULL) = (applied IS NULL)"
            " AND (applied_at IS NULL OR decided_at IS NOT NULL)",
            name=op.f("ck_yt_rising_offers_applied_after_decided"),
        ),
        sa.CheckConstraint(
            "(decision IS NULL) = (decided_at IS NULL)",
            name=op.f("ck_yt_rising_offers_decided_when_decided"),
        ),
        sa.ForeignKeyConstraint(
            ["channel_id"],
            ["yt_channels.channel_id"],
            name=op.f("fk_yt_rising_offers_channel_id_yt_channels"),
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["day"],
            ["yt_rising_offer_days.day"],
            name=op.f("fk_yt_rising_offers_day_yt_rising_offer_days"),
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_yt_rising_offers")),
        sa.UniqueConstraint("channel_id", name="uq_yt_rising_offers_channel"),
    )
    op.create_index(
        "ix_yt_rising_offers_unapplied",
        "yt_rising_offers",
        ["decided_at"],
        unique=False,
        postgresql_where=sa.text(
            "decided_at IS NOT NULL AND applied_at IS NULL"
        ),
    )
    op.create_index(
        "ix_yt_rising_offers_undelivered",
        "yt_rising_offers",
        ["day", "id"],
        unique=False,
        postgresql_where=sa.text("delivered_at IS NULL"),
    )

    for table in METRICS_READS:
        op.execute(f"GRANT SELECT ON {table} TO {BOT}")
    op.execute(
        f"GRANT UPDATE ({', '.join(BOT_UPDATES)}) ON yt_rising_offers TO {BOT}"
    )
    # The chart card's video, read at sending.
    op.execute(f"GRANT SELECT ON yt_raw_charts TO {BOT}")
    for table, columns in METRICS_READS.items():
        op.execute(
            f"GRANT SELECT ({', '.join(columns)}) ON {table} TO {METRICS}"
        )


def downgrade() -> None:
    """Downgrade schema.

    The grants on the two tables go with the tables, the ranking type
    with them; the chart readings stay, so their grant is taken back.
    The discovery source's `chart` stays: Postgres has no DROP VALUE,
    and an unused value strands nothing.
    """
    op.execute(f"REVOKE SELECT ON yt_raw_charts FROM {BOT}")
    op.drop_index(
        "ix_yt_rising_offers_undelivered", table_name="yt_rising_offers"
    )
    op.drop_index(
        "ix_yt_rising_offers_unapplied", table_name="yt_rising_offers"
    )
    op.drop_table("yt_rising_offers")
    op.drop_table("yt_rising_offer_days")
    RANKING.drop(op.get_bind(), checkfirst=True)
