"""drop yt_search_queries.last_asked_at

Revision ID: 7e08e76864ca
Revises: 0eed912b1e41
Create Date: 2026-11-28 04:48:49.186982

``last_asked_at`` was written at every search and read by nothing:
whether a query may be asked again is decided by its runs in date, which
a restore empties, since b5b0c15 moved the decision there and left the
write. A moment kept on the query would answer for an answer the restore
threw away. The column goes; ``first_asked_at`` stays, and with it the
record of which queries were tried.

The downgrade restores the column as it was, NOT NULL, and gives every
row the one moment it knows: a query's last ask is at least its first.
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision: str = "7e08e76864ca"
down_revision: str | Sequence[str] | None = "0eed912b1e41"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.drop_column("yt_search_queries", "last_asked_at")


def downgrade() -> None:
    op.add_column(
        "yt_search_queries",
        sa.Column("last_asked_at", sa.DateTime(timezone=True), nullable=True),
    )
    op.execute("UPDATE yt_search_queries SET last_asked_at = first_asked_at")
    op.alter_column("yt_search_queries", "last_asked_at", nullable=False)
