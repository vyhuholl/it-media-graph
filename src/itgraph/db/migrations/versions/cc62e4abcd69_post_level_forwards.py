"""post level forwards

Revision ID: cc62e4abcd69
Revises: bb62f2bf391f
Create Date: 2026-07-24 12:08:11.913885

"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision: str = "cc62e4abcd69"
down_revision: str | Sequence[str] | None = "bb62f2bf391f"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """Upgrade schema.

    ``edges`` is emptied rather than backfilled. Derivation inserts with
    ``ON CONFLICT DO NOTHING`` and every existing row already satisfies
    the old key, so a re-run would leave the new columns null while
    reporting success — the worst kind of failure. Discarding the derived
    edges outright forces a clean rebuild: ``itgraph derive`` must be run
    after this migration. Nothing is lost — the raw layer is untouched and
    ``pending_mentions`` is left alone, its usernames still unresolved.
    """
    op.drop_constraint(op.f("observed_reference"), "edges", type_="unique")
    op.execute("TRUNCATE TABLE edges")
    op.add_column(
        "edges", sa.Column("dst_msg_id", sa.BigInteger(), nullable=True)
    )
    op.add_column(
        "edges",
        sa.Column(
            "dst_published_at", sa.DateTime(timezone=True), nullable=True
        ),
    )
    op.add_column(
        "edges", sa.Column("grouped_id", sa.BigInteger(), nullable=True)
    )
    # `NULLS NOT DISTINCT` (Postgres 15+) makes the nullable `dst_msg_id`
    # behave in the key: two mention edges with a null post id conflict
    # rather than both inserting, which is what keeps a re-run a no-op.
    op.create_unique_constraint(
        "uq_edges_reference",
        "edges",
        ["src_channel_id", "msg_id", "kind", "dst_channel_id", "dst_msg_id"],
        postgresql_nulls_not_distinct=True,
    )
    op.create_index(
        "ix_edges_dst_channel_id_dst_msg_id",
        "edges",
        ["dst_channel_id", "dst_msg_id"],
        unique=False,
    )


def downgrade() -> None:
    """Downgrade schema.

    The table is emptied here too: post-level rows carry a ``dst_msg_id``
    that the old channel-level key cannot accommodate — two links to
    different posts of one channel would collapse into a duplicate the old
    unique constraint rejects. Dropping the derived edges is safe because
    they are re-derivable; a ``derive`` run repopulates the old shape.
    """
    op.drop_index("ix_edges_dst_channel_id_dst_msg_id", table_name="edges")
    op.drop_constraint("uq_edges_reference", "edges", type_="unique")
    op.execute("TRUNCATE TABLE edges")
    op.drop_column("edges", "grouped_id")
    op.drop_column("edges", "dst_published_at")
    op.drop_column("edges", "dst_msg_id")
    op.create_unique_constraint(
        op.f("observed_reference"),
        "edges",
        ["src_channel_id", "msg_id", "kind", "dst_channel_id"],
    )
