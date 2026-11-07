"""comment counts

Revision ID: ea870c053ba7
Revises: fb31bd0b6656
Create Date: 2026-11-07 03:00:00.000000

Comments are scored as counts from this release: a negative binomial whose
dispersion is fitted per channel kind, because a counter that is zero in
half its readings has no logarithm worth a spread. The dispersion is stored
with the run that measured it, beside the spread the other metrics use.

A comment alert keeps the count that crossed and the expectation it was
scored against. Its z does not give the count back, and the next refresh
would give another expectation; its message states both.

All three nullable, and nothing filled in: a run measured before this
revision scores no comments until the next refresh stores a dispersion,
and an alert raised before it was never a comment alert.
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision: str = "ea870c053ba7"
down_revision: str | Sequence[str] | None = "fb31bd0b6656"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """Upgrade schema."""
    op.add_column(
        "metric_baselines", sa.Column("dispersion", sa.Float(), nullable=True)
    )
    op.add_column("alerts", sa.Column("observed", sa.Float(), nullable=True))
    op.add_column("alerts", sa.Column("expected", sa.Float(), nullable=True))


def downgrade() -> None:
    """Downgrade schema."""
    op.drop_column("alerts", "expected")
    op.drop_column("alerts", "observed")
    op.drop_column("metric_baselines", "dispersion")
