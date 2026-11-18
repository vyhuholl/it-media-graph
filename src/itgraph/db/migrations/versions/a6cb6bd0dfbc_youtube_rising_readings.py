"""youtube rising readings

Revision ID: a6cb6bd0dfbc
Revises: f6308589c4d1
Create Date: 2026-11-18 18:40:12.000000

Three things the YouTube rising rankings need stored:

- ``yt_raw_channel_sections`` stops being a latest-payload table: the
  primary key widens to ``(channel_id, fetched_at)`` and every read
  appends a reading. The rows present are kept exactly as they are —
  each becomes its seed's first reading, the baseline, so nothing is an
  appearance until the second reading after this revision.
- ``yt_raw_charts``: one row per region and fetch, the ``chart=
  mostPopular`` answer trimmed to each video's id and its channel's,
  in chart order. Never rewritten, never removed by age.
- ``yt_lookup_runs``: the scheduled lookup pass's own record, the
  pattern of ``yt_refresh_runs``.

And the exporter reads the two moments each of the new kinds of row it
measures by: a chart reading's region and fetch, a lookup run's start
and end — nothing of either's contents or counts, as
``tests/test_metrics_role.py`` holds.

The downgrade restores the single-column sections key keeping each
seed's newest reading — the older readings of a seed cannot live under
it — and drops both tables with their grants.
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

# revision identifiers, used by Alembic.
revision: str = "a6cb6bd0dfbc"
down_revision: str | Sequence[str] | None = "f6308589c4d1"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

SECTIONS = "yt_raw_channel_sections"
METRICS = "itgraph_metrics"
GRANTS = {
    "yt_raw_charts": ("region", "fetched_at"),
    "yt_lookup_runs": ("started_at", "finished_at"),
}


def upgrade() -> None:
    """Upgrade schema."""
    op.drop_constraint(op.f(f"pk_{SECTIONS}"), SECTIONS, type_="primary")
    op.create_primary_key(
        op.f(f"pk_{SECTIONS}"), SECTIONS, ["channel_id", "fetched_at"]
    )

    op.create_table(
        "yt_raw_charts",
        sa.Column("region", sa.Text(), nullable=False),
        sa.Column("fetched_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column(
            "payload", postgresql.JSONB(astext_type=sa.Text()), nullable=False
        ),
        sa.PrimaryKeyConstraint(
            "region", "fetched_at", name=op.f("pk_yt_raw_charts")
        ),
    )

    op.create_table(
        "yt_lookup_runs",
        sa.Column("id", sa.BigInteger(), nullable=False),
        sa.Column("started_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("finished_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column(
            "outcome",
            postgresql.ENUM(name="yt_pass_outcome", create_type=False),
            nullable=False,
        ),
        sa.Column(
            "tg_requests",
            sa.Integer(),
            server_default=sa.text("0"),
            nullable=False,
        ),
        sa.Column(
            "tg_awaiting",
            sa.Integer(),
            server_default=sa.text("0"),
            nullable=False,
        ),
        sa.Column(
            "mention_requests",
            sa.Integer(),
            server_default=sa.text("0"),
            nullable=False,
        ),
        sa.Column(
            "mention_awaiting",
            sa.Integer(),
            server_default=sa.text("0"),
            nullable=False,
        ),
        sa.Column("error", sa.Text(), nullable=True),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_yt_lookup_runs")),
    )
    op.create_index(
        "ix_yt_lookup_runs_started_at", "yt_lookup_runs", ["started_at"]
    )

    for table, columns in GRANTS.items():
        op.execute(
            f"GRANT SELECT ({', '.join(columns)}) ON {table} TO {METRICS}"
        )


def downgrade() -> None:
    """Downgrade schema.

    The sections key cannot hold two readings of one seed, so the older
    ones go: what the single-row table ever held was the newest reading,
    and that is what it holds again. The chart readings and the lookup
    runs go whole — raw history, rebuilt by nothing, losing nothing the
    rankings promise to keep.
    """
    for table, columns in GRANTS.items():
        op.execute(
            f"REVOKE SELECT ({', '.join(columns)}) ON {table} FROM {METRICS}"
        )

    op.drop_index("ix_yt_lookup_runs_started_at", table_name="yt_lookup_runs")
    op.drop_table("yt_lookup_runs")
    op.drop_table("yt_raw_charts")

    op.execute(
        f"DELETE FROM {SECTIONS} "
        "WHERE (channel_id, fetched_at) NOT IN ("
        "  SELECT channel_id, MAX(fetched_at) FROM "
        + SECTIONS
        + "  GROUP BY channel_id"
        ")"
    )
    op.drop_constraint(op.f(f"pk_{SECTIONS}"), SECTIONS, type_="primary")
    op.create_primary_key(op.f(f"pk_{SECTIONS}"), SECTIONS, ["channel_id"])
