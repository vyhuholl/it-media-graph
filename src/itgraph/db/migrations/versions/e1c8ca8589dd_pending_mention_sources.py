"""pending mention sources

Revision ID: e1c8ca8589dd
Revises: 4bb75804d3cd
Create Date: 2026-07-28 12:37:36.582071

Which channels mention each pending username, so `itgraph resolve` can
spend a daily quota measured in hundreds on the references carrying the
most independent evidence.

Nothing is backfilled here, deliberately. The pairs come from parsing
message payloads, and that parser lives in `derive/references.py` where it
stays re-runnable. A copy of it frozen inside a revision would be a file
that has to keep working forever, and the raw layer refills the table at
any time with one ordinary `itgraph derive` — no network, no rebuild.

Until that pass runs the table is empty and every pending username looks
equally unmentioned. `resolve` says so, rather than letting it read as a
broken ordering.

"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision: str = "e1c8ca8589dd"
down_revision: str | Sequence[str] | None = "4bb75804d3cd"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """Upgrade schema."""
    op.create_table(
        "pending_mention_sources",
        sa.Column("username", sa.Text(), nullable=False),
        sa.Column("channel_id", sa.BigInteger(), nullable=False),
        sa.ForeignKeyConstraint(
            ["username"],
            ["pending_mentions.username"],
            name=op.f("fk_pending_mention_sources_username_pending_mentions"),
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint(
            "username", "channel_id", name=op.f("pk_pending_mention_sources")
        ),
    )


def downgrade() -> None:
    """Downgrade schema.

    Safe to drop: every row is re-derivable from the raw layer, and
    nothing else reads the table.
    """
    op.drop_table("pending_mention_sources")
