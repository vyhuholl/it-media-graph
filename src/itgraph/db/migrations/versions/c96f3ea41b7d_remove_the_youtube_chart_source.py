"""remove the youtube chart source

Revision ID: c96f3ea41b7d
Revises: 24a189012c7b
Create Date: 2026-11-21 14:05:00.000000

The chart source is gone: the regional ``chart=mostPopular`` readings
at category 28 brought noise, not rising channels, and the search is
removed whole (2026-11-21).

**The data.** A decision pressed on a chart card and not yet applied
is applied here first, as the detection pass would have applied it —
the only rows at risk are the ones whose fifteen minutes had not come
round — so nothing the operator pressed is lost. Then the chart offers
go, and with them the ``candidate`` records the source created and
nobody reviewed: that is the operator's own removal, which the
retention rule allows, and leaving them would hold their channels out
of the link ranking for ever, whatever evidence later named them. A
record that was decided — a seed, a maybe, a rejection — keeps its
decision and keeps ``chart`` as its discovery source: the value stays
in ``yt_discovery_source``, written by nothing now, as Postgres has no
DROP VALUE and an unused value strands nothing.

**The schema.** ``yt_raw_charts`` goes, grants and all. The offers
lose the columns only the chart filled — ``ranking``, ``region``,
``position`` — and the days lose ``size_chart``; with them the
``yt_rising_ranking`` type, which nothing references once the column
is gone. The downgrade restores every shape and none of the rows.
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

# revision identifiers, used by Alembic.
revision: str = "c96f3ea41b7d"
down_revision: str | Sequence[str] | None = "24a189012c7b"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

BOT = "itgraph_bot"
METRICS = "itgraph_metrics"
RANKING = postgresql.ENUM("link", "chart", name="yt_rising_ranking")

# A decision pressed on a chart card, landing as `mark_channel` would
# write it on a record that was never a seed: no walk to forget, no
# note kept, a kind or a language only the decision itself carries.
#
# The `chart` literals compare through ::text: on a database migrated
# from scratch the value joins `yt_discovery_source` in the same
# transaction this revision reads it in, and Postgres refuses a value
# that young used as an enum literal.
APPLY_PENDING = """
UPDATE yt_channels AS c
SET status = o.decision,
    kind = coalesce(o.kind, c.kind),
    lang = coalesce(o.language, c.lang),
    reject_reason = CASE WHEN o.decision = 'rejected'
                    THEN o.reject_reason ELSE NULL END,
    reviewed_at = now()
FROM yt_rising_offers AS o
WHERE o.channel_id = c.channel_id
  AND o.ranking::text = 'chart'
  AND o.decided_at IS NOT NULL
  AND o.applied_at IS NULL
  AND c.status IN ('candidate', 'maybe')
"""


def upgrade() -> None:
    """Upgrade schema."""
    op.execute(APPLY_PENDING)
    op.execute("DELETE FROM yt_rising_offers WHERE ranking::text = 'chart'")
    op.execute(
        "DELETE FROM yt_channels "
        "WHERE discovered_via::text = 'chart' AND status = 'candidate'"
    )

    op.drop_column("yt_rising_offers", "position")
    op.drop_column("yt_rising_offers", "region")
    op.drop_column("yt_rising_offers", "ranking")
    op.drop_column("yt_rising_offer_days", "size_chart")
    op.drop_table("yt_raw_charts")
    RANKING.drop(op.get_bind(), checkfirst=True)


def downgrade() -> None:
    """Downgrade schema.

    The shapes come back; the rows the upgrade removed do not — the
    chart readings were raw history rebuilt by nothing, and the offers
    and the unreviewed records are gone as removed, not as pending.
    """
    RANKING.create(op.get_bind(), checkfirst=True)
    op.create_table(
        "yt_raw_charts",
        sa.Column("region", sa.Text(), nullable=False),
        sa.Column("fetched_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column(
            "payload", postgresql.JSONB(astext_type=sa.Text()), nullable=False
        ),
        sa.PrimaryKeyConstraint(
            "region", "fetched_at", name=op.f("pk_yt_raw_charts")
        ),
    )
    op.execute(
        f"GRANT SELECT (region, fetched_at) ON yt_raw_charts TO {METRICS}"
    )
    op.execute(f"GRANT SELECT ON yt_raw_charts TO {BOT}")

    # Every offer left is a link one, which is the default the column
    # carries again; the default goes once the rows have it.
    op.add_column(
        "yt_rising_offers",
        sa.Column(
            "ranking",
            postgresql.ENUM(name="yt_rising_ranking", create_type=False),
            nullable=False,
            server_default="link",
        ),
    )
    op.alter_column(
        "yt_rising_offers",
        "ranking",
        server_default=None,
        existing_nullable=False,
    )
    op.add_column(
        "yt_rising_offers", sa.Column("region", sa.Text(), nullable=True)
    )
    op.add_column(
        "yt_rising_offers", sa.Column("position", sa.Integer(), nullable=True)
    )
    op.add_column(
        "yt_rising_offer_days",
        sa.Column(
            "size_chart", sa.Integer(), nullable=False, server_default="3"
        ),
    )
    op.alter_column(
        "yt_rising_offer_days",
        "size_chart",
        server_default=None,
        existing_nullable=False,
    )
