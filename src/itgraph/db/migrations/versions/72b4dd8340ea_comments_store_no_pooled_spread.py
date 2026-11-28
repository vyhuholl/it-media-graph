"""comments store no pooled spread

Revision ID: 72b4dd8340ea
Revises: 7e08e76864ca
Create Date: 2026-11-28 06:38:13.868521

A count is scored by its dispersion alone: the pooled log spread was
fitted for comments, stored, loaded and read by nothing, and its absence
refused a comments fit whenever the residuals had no measurable spread,
though the dispersion was fitted apart. The refresh stores ``NULL``
there for a counted metric, and the loaders read a missing spread as
"not a ruler", which for comments it never was.

The downgrade restores NOT NULL; comments' stored spreads come back as
the fallback figure the refresh had also fitted, which the loaders of
the older release expect. New rows written after would carry the
dispersion instead.
"""

from collections.abc import Sequence

from alembic import op

# revision identifiers, used by Alembic.
revision: str = "72b4dd8340ea"
down_revision: str | Sequence[str] | None = "7e08e76864ca"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

TABLES = ["tg_metric_baselines", "yt_metric_baselines"]


def upgrade() -> None:
    for table in TABLES:
        op.alter_column(table, "spread", nullable=True)


def downgrade() -> None:
    for table in TABLES:
        op.alter_column(table, "spread", nullable=False)
