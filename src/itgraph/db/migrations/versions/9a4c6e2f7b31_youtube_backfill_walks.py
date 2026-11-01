"""youtube backfill walks

Revision ID: 9a4c6e2f7b31
Revises: 5d2b8e17c4a9
Create Date: 2026-11-01 14:05:00.000000

Where the history walk stands with each channel: when it was walked, how
far, and what the API did — counts only, no API data. Its rows are left
out of every dump by name in `db/backup.py`, because a record restored
without the videos it describes would claim a walk that is not there.

"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision: str = "9a4c6e2f7b31"
down_revision: str | Sequence[str] | None = "5d2b8e17c4a9"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def _counter(name: str) -> sa.Column[int]:
    return sa.Column(
        name, sa.Integer(), server_default=sa.text("0"), nullable=False
    )


def _flag(name: str) -> sa.Column[bool]:
    return sa.Column(
        name, sa.Boolean(), server_default=sa.text("false"), nullable=False
    )


def upgrade() -> None:
    """Upgrade schema."""
    op.create_table(
        "yt_backfill_walks",
        sa.Column("channel_id", sa.Text(), nullable=False),
        sa.Column("cutoff", sa.DateTime(timezone=True), nullable=False),
        sa.Column("started_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("finished_at", sa.DateTime(timezone=True), nullable=True),
        _flag("whole"),
        _counter("pages"),
        _counter("videos"),
        _counter("out_of_order"),
        _counter("undated"),
        _counter("unreturned"),
        _flag("capped"),
        _counter("consecutive_failures"),
        sa.Column("last_error", sa.Text(), nullable=True),
        sa.ForeignKeyConstraint(
            ["channel_id"],
            ["yt_channels.channel_id"],
            name=op.f("fk_yt_backfill_walks_channel_id_yt_channels"),
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint(
            "channel_id", name=op.f("pk_yt_backfill_walks")
        ),
    )


def downgrade() -> None:
    """Downgrade schema.

    Drops the table. Nothing is lost that a walk cannot rebuild, which is
    also what a restore does.
    """
    op.drop_table("yt_backfill_walks")
