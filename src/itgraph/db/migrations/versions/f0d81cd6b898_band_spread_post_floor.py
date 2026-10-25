"""band spread post floor

Revision ID: f0d81cd6b898
Revises: 7fe57a58ffaf
Create Date: 2026-10-25 06:18:18.783500

`baseline_runs.min_band_spread_posts`: how many distinct posts a band
needed before a refresh kept a spread of its own for it.

A run records the parameters it was computed under, and this one decides
which band spreads exist at all — a band under it is divided by the pooled
spread instead. Nullable where `min_band_samples` is not, for the reason
`named handle signal` gave: a run made before this migration fitted band
spreads on readings alone, and a server default would claim it ran under
a rule it never saw.

"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision: str = "f0d81cd6b898"
down_revision: str | Sequence[str] | None = "7fe57a58ffaf"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """Upgrade schema."""
    op.add_column(
        "baseline_runs",
        sa.Column("min_band_spread_posts", sa.Integer(), nullable=True),
    )


def downgrade() -> None:
    """Downgrade schema.

    Drops the parameter and nothing it governed: the band spreads a run
    stored stay, and read the same either way.
    """
    op.drop_column("baseline_runs", "min_band_spread_posts")
