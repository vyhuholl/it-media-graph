"""youtube baselines

Revision ID: 5e7cef4aa5d0
Revises: 9bd22bfcc8b8
Create Date: 2026-11-11 23:44:39.931445

What a YouTube video of a given format and age is expected to reach, per
refresh: Telegram's four baseline tables with the format beside the kind,
in tables of YouTube's own, since a channel here is a `text` id.

Two new types, `yt_metric` and `yt_video_format`. Two existing ones are
reused rather than copied: `yt_channel_kind`, and Telegram's
`baseline_reference`, whose two values — a channel's own newest posts, or
its settled ones — mean the same on both platforms.

**The grants.** The exporter reads, of each platform's baseline runs, when
a run completed and how many channels it counted — and no reference, curve
or factor. The reader takes the new tables through its default privileges.
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

# revision identifiers, used by Alembic.
revision: str = "5e7cef4aa5d0"
down_revision: str | Sequence[str] | None = "9bd22bfcc8b8"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

METRICS = "itgraph_metrics"

YT_METRIC = postgresql.ENUM("views", "likes", "comments", name="yt_metric")
YT_VIDEO_FORMAT = postgresql.ENUM("short", "long", name="yt_video_format")
NEW_TYPES = (YT_METRIC, YT_VIDEO_FORMAT)

# What the series need of a run, on either platform.
METRICS_READS = ("completed_at", "channels_in_scope", "channels_with_baseline")
RUN_TABLES = ("baseline_runs", "yt_baseline_runs")


def _type(name: str) -> postgresql.ENUM:
    return postgresql.ENUM(name=name, create_type=False)


def _run_id() -> sa.Column[int]:
    return sa.Column("run_id", sa.BigInteger(), nullable=False)


def _run_key(table: str) -> sa.ForeignKeyConstraint:
    return sa.ForeignKeyConstraint(
        ["run_id"],
        ["yt_baseline_runs.id"],
        name=op.f(f"fk_{table}_run_id_yt_baseline_runs"),
        ondelete="CASCADE",
    )


def upgrade() -> None:
    """Upgrade schema."""
    bind = op.get_bind()
    for type_ in NEW_TYPES:
        type_.create(bind, checkfirst=True)

    op.create_table(
        "yt_baseline_runs",
        sa.Column("id", sa.BigInteger(), nullable=False),
        sa.Column(
            "started_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column("completed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("window_days", sa.Integer(), nullable=False),
        sa.Column("mature_days", sa.Integer(), nullable=False),
        sa.Column("mature_max_days", sa.Integer(), nullable=False),
        sa.Column("min_channel_videos", sa.Integer(), nullable=False),
        sa.Column("recent_videos", sa.Integer(), nullable=False),
        sa.Column("recent_min_videos", sa.Integer(), nullable=False),
        sa.Column("recent_max_days", sa.Integer(), nullable=False),
        sa.Column("min_band_samples", sa.Integer(), nullable=False),
        sa.Column("min_band_spread_posts", sa.Integer(), nullable=False),
        sa.Column("channels_in_scope", sa.Integer(), nullable=False),
        sa.Column("channels_with_baseline", sa.Integer(), nullable=False),
        sa.Column("channels_on_fallback", sa.Integer(), nullable=False),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_yt_baseline_runs")),
    )
    op.create_table(
        "yt_channel_baselines",
        _run_id(),
        sa.Column("channel_id", sa.Text(), nullable=False),
        sa.Column("format", _type("yt_video_format"), nullable=False),
        sa.Column("metric", _type("yt_metric"), nullable=False),
        sa.Column("median", sa.Float(), nullable=False),
        sa.Column("samples", sa.Integer(), nullable=False),
        sa.Column("reference", _type("baseline_reference"), nullable=False),
        sa.ForeignKeyConstraint(
            ["channel_id"],
            ["yt_channels.channel_id"],
            name=op.f("fk_yt_channel_baselines_channel_id_yt_channels"),
            ondelete="CASCADE",
        ),
        _run_key("yt_channel_baselines"),
        sa.PrimaryKeyConstraint(
            "run_id",
            "channel_id",
            "format",
            "metric",
            name=op.f("pk_yt_channel_baselines"),
        ),
    )
    op.create_table(
        "yt_curve_points",
        _run_id(),
        sa.Column("kind", _type("yt_channel_kind"), nullable=False),
        sa.Column("format", _type("yt_video_format"), nullable=False),
        sa.Column("metric", _type("yt_metric"), nullable=False),
        sa.Column("band", sa.Text(), nullable=False),
        sa.Column("fraction", sa.Float(), nullable=False),
        sa.Column("spread", sa.Float(), nullable=True),
        sa.Column("samples", sa.Integer(), nullable=False),
        _run_key("yt_curve_points"),
        sa.PrimaryKeyConstraint(
            "run_id",
            "kind",
            "format",
            "metric",
            "band",
            name=op.f("pk_yt_curve_points"),
        ),
    )
    op.create_table(
        "yt_metric_baselines",
        _run_id(),
        sa.Column("kind", _type("yt_channel_kind"), nullable=False),
        sa.Column("format", _type("yt_video_format"), nullable=False),
        sa.Column("metric", _type("yt_metric"), nullable=False),
        sa.Column("factor", sa.Float(), nullable=False),
        sa.Column("spread", sa.Float(), nullable=False),
        sa.Column("samples", sa.Integer(), nullable=False),
        sa.Column("dispersion", sa.Float(), nullable=True),
        sa.Column(
            "borrowed",
            sa.Boolean(),
            server_default=sa.text("false"),
            nullable=False,
        ),
        _run_key("yt_metric_baselines"),
        sa.PrimaryKeyConstraint(
            "run_id",
            "kind",
            "format",
            "metric",
            name=op.f("pk_yt_metric_baselines"),
        ),
    )

    for table in RUN_TABLES:
        op.execute(
            f"GRANT SELECT ({', '.join(METRICS_READS)}) ON {table} TO {METRICS}"
        )


def downgrade() -> None:
    """Downgrade schema.

    The grant on Telegram's runs is revoked; YouTube's goes with its table.
    """
    op.execute(
        f"REVOKE SELECT ({', '.join(METRICS_READS)}) ON baseline_runs "
        f"FROM {METRICS}"
    )
    op.drop_table("yt_metric_baselines")
    op.drop_table("yt_curve_points")
    op.drop_table("yt_channel_baselines")
    op.drop_table("yt_baseline_runs")

    bind = op.get_bind()
    for type_ in reversed(NEW_TYPES):
        type_.drop(bind, checkfirst=True)
