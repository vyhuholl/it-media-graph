"""youtube discovery tables

Revision ID: a89cb273f885
Revises: f25cff672b9d
Create Date: 2026-11-01 16:23:55.808352

What YouTube's own sources of candidates keep:

- `yt_raw_channel_sections`: the channel ids each seed lists in its
  channel sections.
- `yt_search_queries`: the operator's queries and when they were asked.
  The operator's own, kept and dumped.
- `yt_search_runs`: one search of one query.
- `yt_raw_search_results`: each result's channel id and position. API
  data.

And `yt_quota_exhausted` gains the bucket a `quotaExceeded` came from:
the search bucket is Google's own since 2026-06-01, and its exhaustion
must not end the main bucket's day.

"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

# revision identifiers, used by Alembic.
revision: str = "a89cb273f885"
down_revision: str | Sequence[str] | None = "f25cff672b9d"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

YT_QUOTA_BUCKET = postgresql.ENUM("main", "search", name="yt_quota_bucket")
YT_SEARCH_TYPE = postgresql.ENUM("video", "channel", name="yt_search_type")


def _column_type(enum: postgresql.ENUM) -> postgresql.ENUM:
    """The enum as a column type, without creating it a second time."""
    return postgresql.ENUM(*enum.enums, name=enum.name, create_type=False)


def upgrade() -> None:
    """Upgrade schema.

    Existing exhaustion rows were all heard on the main bucket — no
    search was ever made before this revision — so the column defaults
    to `main` and the primary key widens to the day and the bucket.
    """
    bind = op.get_bind()
    YT_QUOTA_BUCKET.create(bind)
    YT_SEARCH_TYPE.create(bind)

    op.add_column(
        "yt_quota_exhausted",
        sa.Column(
            "bucket",
            _column_type(YT_QUOTA_BUCKET),
            server_default="main",
            nullable=False,
        ),
    )
    op.drop_constraint(
        "pk_yt_quota_exhausted", "yt_quota_exhausted", type_="primary"
    )
    op.create_primary_key(
        "pk_yt_quota_exhausted", "yt_quota_exhausted", ["day", "bucket"]
    )

    op.create_table(
        "yt_raw_channel_sections",
        sa.Column("channel_id", sa.Text(), nullable=False),
        sa.Column(
            "payload", postgresql.JSONB(astext_type=sa.Text()), nullable=False
        ),
        sa.Column("fetched_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(
            ["channel_id"],
            ["yt_channels.channel_id"],
            name=op.f("fk_yt_raw_channel_sections_channel_id_yt_channels"),
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint(
            "channel_id", name=op.f("pk_yt_raw_channel_sections")
        ),
    )
    op.create_index(
        "ix_yt_raw_channel_sections_fetched_at",
        "yt_raw_channel_sections",
        ["fetched_at"],
    )

    op.create_table(
        "yt_search_queries",
        sa.Column("id", sa.BigInteger(), nullable=False),
        sa.Column("query", sa.Text(), nullable=False),
        sa.Column("type", _column_type(YT_SEARCH_TYPE), nullable=False),
        sa.Column(
            "first_asked_at", sa.DateTime(timezone=True), nullable=False
        ),
        sa.Column("last_asked_at", sa.DateTime(timezone=True), nullable=False),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_yt_search_queries")),
        sa.UniqueConstraint(
            "query", "type", name=op.f("uq_yt_search_queries_query")
        ),
    )

    op.create_table(
        "yt_search_runs",
        sa.Column("id", sa.BigInteger(), nullable=False),
        sa.Column("query_id", sa.BigInteger(), nullable=False),
        sa.Column("asked_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column(
            "published_after", sa.DateTime(timezone=True), nullable=True
        ),
        sa.Column(
            "pages", sa.Integer(), server_default=sa.text("0"), nullable=False
        ),
        sa.ForeignKeyConstraint(
            ["query_id"],
            ["yt_search_queries.id"],
            name=op.f("fk_yt_search_runs_query_id_yt_search_queries"),
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_yt_search_runs")),
    )
    op.create_index(
        "ix_yt_search_runs_asked_at", "yt_search_runs", ["asked_at"]
    )

    op.create_table(
        "yt_raw_search_results",
        sa.Column("run_id", sa.BigInteger(), nullable=False),
        sa.Column("position", sa.Integer(), nullable=False),
        sa.Column("channel_id", sa.Text(), nullable=False),
        sa.CheckConstraint(
            "channel_id ~ '^UC[0-9A-Za-z_-]{22}$'",
            name=op.f("ck_yt_raw_search_results_channel_shape"),
        ),
        sa.CheckConstraint(
            "position >= 1",
            name=op.f("ck_yt_raw_search_results_position_from_one"),
        ),
        sa.ForeignKeyConstraint(
            ["run_id"],
            ["yt_search_runs.id"],
            name=op.f("fk_yt_raw_search_results_run_id_yt_search_runs"),
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint(
            "run_id", "position", name=op.f("pk_yt_raw_search_results")
        ),
    )


def downgrade() -> None:
    """Downgrade schema.

    Drops the four tables and their types. Exhaustion heard on the
    search bucket has no place in the narrower key, so it is deleted
    first: a search bucket no command can reach any more ends no day.
    Nothing is lost that YouTube cannot be asked again, except the
    operator's record of queries.
    """
    op.drop_table("yt_raw_search_results")
    op.drop_index("ix_yt_search_runs_asked_at", table_name="yt_search_runs")
    op.drop_table("yt_search_runs")
    op.drop_table("yt_search_queries")
    op.drop_index(
        "ix_yt_raw_channel_sections_fetched_at",
        table_name="yt_raw_channel_sections",
    )
    op.drop_table("yt_raw_channel_sections")

    op.execute("DELETE FROM yt_quota_exhausted WHERE bucket = 'search'")
    op.drop_constraint(
        "pk_yt_quota_exhausted", "yt_quota_exhausted", type_="primary"
    )
    op.create_primary_key(
        "pk_yt_quota_exhausted", "yt_quota_exhausted", ["day"]
    )
    op.drop_column("yt_quota_exhausted", "bucket")

    bind = op.get_bind()
    YT_SEARCH_TYPE.drop(bind)
    YT_QUOTA_BUCKET.drop(bind)
