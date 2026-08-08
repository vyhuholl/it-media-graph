"""per-band spread and borrowed curves

Revision ID: 4073834541f2
Revises: f39ba7c051de
Create Date: 2026-08-08 22:52:15.293559

Two columns, both recording something a run previously could not say.

`curve_points.spread` is the dispersion measured for one age band. It is
nullable and stays null where the band had too few residuals to measure
its own — absent rather than filled with the pooled figure, so "not
measured here" is still legible a month later instead of looking like a
measurement that happened to agree.

`metric_baselines.borrowed` records that this kind took the curve pooled
across all kinds because it could not support one of its own. Existing
rows are false, which is correct: every baseline written before this
migration was fitted from its own kind.

Neither column is backfilled. A refresh replaces rather than
accumulates, so the next run fills them for the baselines anyone will
actually score against, and the older runs keep saying exactly what they
were able to say at the time.
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision: str = "4073834541f2"
down_revision: str | Sequence[str] | None = "f39ba7c051de"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """Upgrade schema."""
    op.add_column(
        "curve_points", sa.Column("spread", sa.Float(), nullable=True)
    )
    op.add_column(
        "metric_baselines",
        sa.Column(
            "borrowed",
            sa.Boolean(),
            server_default=sa.text("false"),
            nullable=False,
        ),
    )


def downgrade() -> None:
    """Downgrade schema."""
    op.drop_column("metric_baselines", "borrowed")
    op.drop_column("curve_points", "spread")
