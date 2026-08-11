"""named handle signal

Revision ID: 7a1e3c9d40b2
Revises: 4073834541f2
Create Date: 2026-08-11 12:04:41.882317

Four columns for a fifth affiliation signal: a username token that a
channel carrying it names as a handle in its own description.

`affiliation_candidates` gains the handle and how many channels carry it,
in the shape `shared_token` / `shared_token_channels` already uses. Both
repeat across every pair of one group, which is what lets the review list
be grouped with a `GROUP BY` rather than a second table.

`affiliation_runs` gains the signal's cap and its weight, and these two
are nullable where every other parameter on that table is not. A run
recorded before this migration genuinely had no such parameter; a server
default would claim it ran under one it never saw.

Nothing is backfilled and nothing existing changes shape. The first
detection run after the upgrade fills the evidence for the pairs it
proposes, and the older runs keep saying exactly what they could say at
the time.

The downgrade drops these four columns and nothing else. No decision
column is touched, so a rollback costs one re-run of `itgraph
affiliates` and no review.
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision: str = "7a1e3c9d40b2"
down_revision: str | Sequence[str] | None = "4073834541f2"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """Upgrade schema."""
    op.add_column(
        "affiliation_candidates",
        sa.Column("handle_token", sa.Text(), nullable=True),
    )
    op.add_column(
        "affiliation_candidates",
        sa.Column("handle_token_channels", sa.Integer(), nullable=True),
    )
    op.add_column(
        "affiliation_runs",
        sa.Column("max_handle_token_channels", sa.Integer(), nullable=True),
    )
    op.add_column(
        "affiliation_runs",
        sa.Column("weight_handle", sa.Float(), nullable=True),
    )


def downgrade() -> None:
    """Downgrade schema."""
    op.drop_column("affiliation_runs", "weight_handle")
    op.drop_column("affiliation_runs", "max_handle_token_channels")
    op.drop_column("affiliation_candidates", "handle_token_channels")
    op.drop_column("affiliation_candidates", "handle_token")
