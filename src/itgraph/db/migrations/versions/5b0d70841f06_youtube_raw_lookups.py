"""youtube raw lookups

Revision ID: 5b0d70841f06
Revises: b7f8952801f5
Create Date: 2026-11-01 12:45:52.565825

Which channel YouTube said a linked video, handle or legacy name belongs
to. API data, so it takes the `yt_raw_` prefix, and with it the dump
exclusion in `db/backup.py` and the 30-day expiry every YouTube command
runs first.

"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

# revision identifiers, used by Alembic.
revision: str = "5b0d70841f06"
down_revision: str | Sequence[str] | None = "b7f8952801f5"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

YT_LOOKUP_KIND = postgresql.ENUM(
    "video", "handle", "legacy", name="yt_lookup_kind"
)


def upgrade() -> None:
    """Upgrade schema."""
    YT_LOOKUP_KIND.create(op.get_bind())
    op.create_table(
        "yt_raw_lookups",
        sa.Column(
            "kind",
            postgresql.ENUM(
                *YT_LOOKUP_KIND.enums,
                name=YT_LOOKUP_KIND.name,
                create_type=False,
            ),
            nullable=False,
        ),
        sa.Column("value", sa.Text(), nullable=False),
        sa.Column("channel_id", sa.Text(), nullable=True),
        sa.Column("fetched_at", sa.DateTime(timezone=True), nullable=False),
        sa.CheckConstraint(
            "channel_id IS NULL OR channel_id ~ '^UC[0-9A-Za-z_-]{22}$'",
            name=op.f("ck_yt_raw_lookups_channel_shape"),
        ),
        sa.CheckConstraint(
            "kind <> 'video' OR value ~ '^[0-9A-Za-z_-]{11}$'",
            name=op.f("ck_yt_raw_lookups_video_shape"),
        ),
        sa.PrimaryKeyConstraint(
            "kind", "value", name=op.f("pk_yt_raw_lookups")
        ),
    )
    op.create_index(
        "ix_yt_raw_lookups_fetched_at", "yt_raw_lookups", ["fetched_at"]
    )


def downgrade() -> None:
    """Downgrade schema.

    Drops the table and its type. Nothing is lost that a lookup run
    cannot ask again, which is also what a restore does.
    """
    op.drop_index("ix_yt_raw_lookups_fetched_at", table_name="yt_raw_lookups")
    op.drop_table("yt_raw_lookups")
    YT_LOOKUP_KIND.drop(op.get_bind())
