"""baseline tables

Revision ID: 48d4cce98618
Revises: d81c4f6a02b7
Create Date: 2026-08-06 09:12:44.180233

What a post of a given age is expected to reach, so that a reading can be
scored against it. Four tables because there are four grains and none of
them nests inside another: a channel's median is per channel and metric,
the factor and the spread are per kind and metric, and a curve point is
per kind, metric and age band. Repeating any of them across a coarser
table would be one fact stored many times, and one place for two copies
to disagree.

`baseline_runs` is what makes a refresh replace rather than accumulate:
everything points at a run, and reading baselines means reading the newest
completed one. Older rows stay, so a threshold argued about next month can
be compared against the baselines it was actually arguing with.

Additive; the downgrade is a clean drop. It also drops the `metric` enum
type, which `drop_table` does not do on its own — see the alert queue
revision for what that omission costs.

"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

# revision identifiers, used by Alembic.
revision: str = "48d4cce98618"
down_revision: str | Sequence[str] | None = "d81c4f6a02b7"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

# Named so the downgrade can drop it by the same spelling that created it.
METRIC = sa.Enum("views", "reactions", "forwards", "comments", name="metric")

# `channel_kind` already exists — the inventory revision created it. A
# bare `sa.Enum` here would have `create_table` emit `CREATE TYPE` for it
# again, which succeeds only while nothing has ever exercised the
# downgrade: the first upgrade of a fresh database happens to run before
# this table, and the second one fails on `type already exists`. So the
# type is referenced, not declared.
CHANNEL_KIND = postgresql.ENUM(name="channel_kind", create_type=False)


def upgrade() -> None:
    """Upgrade schema."""

    op.create_table(
        "baseline_runs",
        sa.Column("id", sa.BigInteger(), nullable=False),
        sa.Column(
            "started_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column("completed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("mature_days", sa.Integer(), nullable=False),
        sa.Column("min_channel_posts", sa.Integer(), nullable=False),
        sa.Column("min_band_samples", sa.Integer(), nullable=False),
        sa.Column("channels_in_scope", sa.Integer(), nullable=False),
        sa.Column("channels_with_baseline", sa.Integer(), nullable=False),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_baseline_runs")),
    )
    op.create_table(
        "channel_baselines",
        sa.Column("run_id", sa.BigInteger(), nullable=False),
        sa.Column(
            "channel_id", sa.BigInteger(), autoincrement=False, nullable=False
        ),
        sa.Column("metric", METRIC, nullable=False),
        sa.Column("median", sa.Float(), nullable=False),
        sa.Column("samples", sa.Integer(), nullable=False),
        sa.ForeignKeyConstraint(
            ["channel_id"],
            ["channels.tg_id"],
            name=op.f("fk_channel_baselines_channel_id_channels"),
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["run_id"],
            ["baseline_runs.id"],
            name=op.f("fk_channel_baselines_run_id_baseline_runs"),
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint(
            "run_id", "channel_id", "metric", name=op.f("pk_channel_baselines")
        ),
    )
    op.create_table(
        "curve_points",
        sa.Column("run_id", sa.BigInteger(), nullable=False),
        sa.Column(
            "kind",
            CHANNEL_KIND,
            nullable=False,
        ),
        sa.Column("metric", METRIC, nullable=False),
        sa.Column("band", sa.Text(), nullable=False),
        sa.Column("fraction", sa.Float(), nullable=False),
        sa.Column("samples", sa.Integer(), nullable=False),
        sa.ForeignKeyConstraint(
            ["run_id"],
            ["baseline_runs.id"],
            name=op.f("fk_curve_points_run_id_baseline_runs"),
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint(
            "run_id", "kind", "metric", "band", name=op.f("pk_curve_points")
        ),
    )
    op.create_table(
        "metric_baselines",
        sa.Column("run_id", sa.BigInteger(), nullable=False),
        sa.Column(
            "kind",
            CHANNEL_KIND,
            nullable=False,
        ),
        sa.Column("metric", METRIC, nullable=False),
        sa.Column("factor", sa.Float(), nullable=False),
        sa.Column("spread", sa.Float(), nullable=False),
        sa.Column("samples", sa.Integer(), nullable=False),
        sa.ForeignKeyConstraint(
            ["run_id"],
            ["baseline_runs.id"],
            name=op.f("fk_metric_baselines_run_id_baseline_runs"),
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint(
            "run_id", "kind", "metric", name=op.f("pk_metric_baselines")
        ),
    )


def downgrade() -> None:
    """Downgrade schema."""

    op.drop_table("metric_baselines")
    op.drop_table("curve_points")
    op.drop_table("channel_baselines")
    op.drop_table("baseline_runs")

    # `drop_table` leaves the type behind, and a later upgrade then
    # fails on `type "metric" already exists` — a failure that only
    # appears when somebody exercises the downgrade.
    METRIC.drop(op.get_bind(), checkfirst=True)
