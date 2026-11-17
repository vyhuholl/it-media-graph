"""rising candidates

Revision ID: b13d2bd31424
Revises: f07160617ce5
Create Date: 2026-11-17 12:11:53.155886

What Telegram's rising candidates need of the schema.

**A pending source remembers when.** `first_referenced_at` and
`last_referenced_at` on `tg_pending_mention_sources`: the publication times
of the first and the last message in which that channel mentioned the
name. Nullable: the first derivation after this revision fills every row,
since every derivation reads the whole raw layer.

**A journal of resolutions.** `tg_resolutions`, one row per resolution by
`itgraph resolve`, `itgraph add` or the poll loop: when, by which command,
by id or by username, and what it found. No username and no id — a name
can be a person's. The poll loop's cap is read from it.

**The grants.** The exporter reads when a resolution was made and by which
command, and nothing else of it. The reader takes the table through its
default privileges. Nothing on a restore: the journal names nothing and
nothing acts on it.
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

# revision identifiers, used by Alembic.
revision: str = "b13d2bd31424"
down_revision: str | Sequence[str] | None = "f07160617ce5"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

METRICS = "itgraph_metrics"
# What the series needs of a resolution.
METRICS_READS = ("requested_at", "command")

KIND = postgresql.ENUM("id", "username", name="tg_resolution_kind")
OUTCOME = postgresql.ENUM(
    "channel", "not_channel", "failed", name="tg_resolution_outcome"
)


def upgrade() -> None:
    """Upgrade schema."""
    KIND.create(op.get_bind())
    OUTCOME.create(op.get_bind())
    op.create_table(
        "tg_resolutions",
        sa.Column("id", sa.BigInteger(), nullable=False),
        sa.Column(
            "requested_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column(
            "command",
            postgresql.ENUM(name="tg_collection_command", create_type=False),
            nullable=False,
        ),
        sa.Column(
            "kind",
            postgresql.ENUM(name="tg_resolution_kind", create_type=False),
            nullable=False,
        ),
        sa.Column(
            "outcome",
            postgresql.ENUM(name="tg_resolution_outcome", create_type=False),
            nullable=False,
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_tg_resolutions")),
    )
    op.create_index(
        "ix_tg_resolutions_requested_at",
        "tg_resolutions",
        ["requested_at"],
        unique=False,
    )
    op.add_column(
        "tg_pending_mention_sources",
        sa.Column(
            "first_referenced_at", sa.DateTime(timezone=True), nullable=True
        ),
    )
    op.add_column(
        "tg_pending_mention_sources",
        sa.Column(
            "last_referenced_at", sa.DateTime(timezone=True), nullable=True
        ),
    )
    op.execute(
        f"GRANT SELECT ({', '.join(METRICS_READS)}) ON tg_resolutions "
        f"TO {METRICS}"
    )


def downgrade() -> None:
    """Downgrade schema.

    The grant goes with the table.
    """
    op.drop_column("tg_pending_mention_sources", "last_referenced_at")
    op.drop_column("tg_pending_mention_sources", "first_referenced_at")
    op.drop_index(
        "ix_tg_resolutions_requested_at", table_name="tg_resolutions"
    )
    op.drop_table("tg_resolutions")
    OUTCOME.drop(op.get_bind())
    KIND.drop(op.get_bind())
