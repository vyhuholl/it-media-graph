"""youtube alert queue

Revision ID: 9bd22bfcc8b8
Revises: 4678f31aac4f
Create Date: 2026-11-11 12:44:05.817432

YouTube's alerts, in a table of their own beside Telegram's `alerts`,
which references `raw_messages`: an alert about something Telegram never
collected should stay unrepresentable there.

**An alert lives as long as the reading it was scored on**, by a foreign
key with `ON DELETE CASCADE` to `yt_raw_video_stats`, which goes with its
video's payload. So a video YouTube stops returning takes its alerts at
once, expiry stays one `DELETE`, and no clock of this table's own can keep
a video named past its data. Dumps leave the table out by name, as they
leave out `yt_search_runs`.

**The grants.** The bot reads what a YouTube message is made of — the
inventory's labels, the payloads, the readings, when the poll loop last
completed — and may change only an alert's delivery state: not raise one,
and not touch what it is about or the score that raised it. The exporter
reads an alert's moments and delivery state, and not which video it is
about nor its score.
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

# revision identifiers, used by Alembic.
revision: str = "9bd22bfcc8b8"
down_revision: str | Sequence[str] | None = "4678f31aac4f"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

BOT = "itgraph_bot"
METRICS = "itgraph_metrics"

YT_ALERT_KIND = postgresql.ENUM(
    "views_spike", "like_spike", "comment_spike", name="yt_alert_kind"
)
YT_ALERT_DELIVERY = postgresql.ENUM(
    "direct", "digest", "folded", "excluded", name="yt_alert_delivery"
)
YT_ALERT_ROUTE = postgresql.ENUM("en", "ru", name="yt_alert_route")
NEW_TYPES = (YT_ALERT_KIND, YT_ALERT_DELIVERY, YT_ALERT_ROUTE)

# What the bot renders a YouTube message from, and asks `/status` of.
BOT_READS = (
    "yt_alerts",
    "yt_channels",
    "yt_raw_channels",
    "yt_raw_videos",
    "yt_raw_video_stats",
    "yt_watch_passes",
)
# An alert's delivery state, and nothing it was raised with.
BOT_UPDATES = (
    "held_at",
    "delivered_at",
    "delivery",
    "route",
    "attempts",
    "last_error",
)
# What the series need of an alert: when, and whether it is out.
METRICS_READS = (
    "raised_at",
    "held_at",
    "delivered_at",
    "delivery",
    "attempts",
    "folded_into",
)


def upgrade() -> None:
    """Upgrade schema."""
    bind = op.get_bind()
    for type_ in NEW_TYPES:
        type_.create(bind, checkfirst=True)

    op.create_table(
        "yt_alerts",
        sa.Column("id", sa.BigInteger(), nullable=False),
        sa.Column(
            "kind",
            postgresql.ENUM(name="yt_alert_kind", create_type=False),
            nullable=False,
        ),
        sa.Column("video_id", sa.Text(), nullable=False),
        sa.Column("scored_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("band", sa.Integer(), nullable=False),
        sa.Column("value", sa.Float(), nullable=False),
        sa.Column("expected", sa.Float(), nullable=True),
        sa.Column(
            "raised_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column("held_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("delivered_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column(
            "delivery",
            postgresql.ENUM(name="yt_alert_delivery", create_type=False),
            nullable=True,
        ),
        sa.Column(
            "route",
            postgresql.ENUM(name="yt_alert_route", create_type=False),
            nullable=True,
        ),
        sa.Column(
            "attempts",
            sa.Integer(),
            server_default=sa.text("0"),
            nullable=False,
        ),
        sa.Column("last_error", sa.Text(), nullable=True),
        sa.Column("folded_into", sa.BigInteger(), nullable=True),
        sa.CheckConstraint(
            "folded_into <> id",
            name=op.f("ck_yt_alerts_not_folded_into_itself"),
        ),
        sa.ForeignKeyConstraint(
            ["folded_into"],
            ["yt_alerts.id"],
            name=op.f("fk_yt_alerts_folded_into_yt_alerts"),
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["video_id", "scored_at"],
            ["yt_raw_video_stats.video_id", "yt_raw_video_stats.observed_at"],
            name=op.f("fk_yt_alerts_video_id_yt_raw_video_stats"),
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_yt_alerts")),
        sa.UniqueConstraint(
            "kind", "video_id", "band", name="uq_yt_alerts_video_band"
        ),
    )
    op.create_index(
        "ix_yt_alerts_undelivered",
        "yt_alerts",
        ["raised_at"],
        unique=False,
        postgresql_where=sa.text("delivered_at IS NULL"),
    )
    op.create_index(
        "ix_yt_alerts_folded_into",
        "yt_alerts",
        ["folded_into"],
        unique=False,
        postgresql_where=sa.text("folded_into IS NOT NULL"),
    )

    for table in BOT_READS:
        op.execute(f"GRANT SELECT ON {table} TO {BOT}")
    op.execute(
        f"GRANT UPDATE ({', '.join(BOT_UPDATES)}) ON yt_alerts TO {BOT}"
    )
    op.execute(
        f"GRANT SELECT ({', '.join(METRICS_READS)}) ON yt_alerts TO {METRICS}"
    )


def downgrade() -> None:
    """Downgrade schema.

    The grants on the tables that stay are revoked; those on the queue go
    with it.
    """
    for table in BOT_READS:
        if table != "yt_alerts":
            op.execute(f"REVOKE SELECT ON {table} FROM {BOT}")
    op.drop_index(
        "ix_yt_alerts_folded_into",
        table_name="yt_alerts",
        postgresql_where=sa.text("folded_into IS NOT NULL"),
    )
    op.drop_index(
        "ix_yt_alerts_undelivered",
        table_name="yt_alerts",
        postgresql_where=sa.text("delivered_at IS NULL"),
    )
    op.drop_table("yt_alerts")

    bind = op.get_bind()
    for type_ in reversed(NEW_TYPES):
        type_.drop(bind, checkfirst=True)
