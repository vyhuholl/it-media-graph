"""exporter reads seeds awaiting their walk

Revision ID: f07160617ce5
Revises: e4df494b6fc4
Create Date: 2026-11-17 05:37:07.753967

The poll loop now walks a new seed's history by itself, and two series
tell the shared monitoring how many seeds still await their walk and how
long the earliest has waited since it was marked a seed.

**Two columns, and nothing else of the inventory.** The exporter already
reads a channel's id, status and whether it is a chat, and a walk's
channel and the kind of its failure. It gains when a channel was reviewed
and the status of its walk. It still reads no username, title, kind or
note, and no cursor, cutoff or failure detail; `tests/test_metrics_role.py`
holds it to that.
"""

from collections.abc import Sequence

from alembic import op

# revision identifiers, used by Alembic.
revision: str = "f07160617ce5"
down_revision: str | Sequence[str] | None = "e4df494b6fc4"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

METRICS = "itgraph_metrics"
READS = {
    "tg_channels": ("reviewed_at",),
    "tg_backfill_state": ("status",),
}


def upgrade() -> None:
    for table, columns in READS.items():
        op.execute(
            f"GRANT SELECT ({', '.join(columns)}) ON {table} TO {METRICS}"
        )


def downgrade() -> None:
    for table, columns in READS.items():
        op.execute(
            f"REVOKE SELECT ({', '.join(columns)}) ON {table} FROM {METRICS}"
        )
