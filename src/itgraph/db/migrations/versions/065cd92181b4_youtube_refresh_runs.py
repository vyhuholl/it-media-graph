"""youtube refresh runs

Revision ID: 065cd92181b4
Revises: 549774d4e09c
Create Date: 2026-11-16 08:01:06.613481

One row per run of `yt refresh`: when it started and ended, how it ended
(the poll loop's `yt_pass_outcome`, reused) and how many late readings
of past uploads were due and taken.

**The grants.** The exporter reads when a run ended and how, and nothing
of its counts or its error. The reader takes the table through its
default privileges.
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

# revision identifiers, used by Alembic.
revision: str = "065cd92181b4"
down_revision: str | Sequence[str] | None = "549774d4e09c"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

METRICS = "itgraph_metrics"

# What the series needs of a run.
METRICS_READS = ("finished_at", "outcome")


def upgrade() -> None:
    """Upgrade schema."""
    op.create_table(
        "yt_refresh_runs",
        sa.Column("id", sa.BigInteger(), nullable=False),
        sa.Column("started_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("finished_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column(
            "outcome",
            postgresql.ENUM(name="yt_pass_outcome", create_type=False),
            nullable=False,
        ),
        sa.Column(
            "late_due",
            sa.Integer(),
            server_default=sa.text("0"),
            nullable=False,
        ),
        sa.Column(
            "late_read",
            sa.Integer(),
            server_default=sa.text("0"),
            nullable=False,
        ),
        sa.Column("error", sa.Text(), nullable=True),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_yt_refresh_runs")),
    )
    op.create_index(
        "ix_yt_refresh_runs_started_at",
        "yt_refresh_runs",
        ["started_at"],
        unique=False,
    )
    op.execute(
        f"GRANT SELECT ({', '.join(METRICS_READS)}) ON yt_refresh_runs "
        f"TO {METRICS}"
    )


def downgrade() -> None:
    """Downgrade schema.

    The grant goes with the table.
    """
    op.drop_index(
        "ix_yt_refresh_runs_started_at", table_name="yt_refresh_runs"
    )
    op.drop_table("yt_refresh_runs")
