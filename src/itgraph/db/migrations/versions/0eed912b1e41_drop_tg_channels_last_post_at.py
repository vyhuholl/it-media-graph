"""drop tg_channels.last_post_at

Revision ID: 0eed912b1e41
Revises: 04ec0c882427
Create Date: 2026-11-28 03:22:25.349613

``tg_channels.last_post_at`` was denormalized from the newest message a
walk saw, "a convenience for is this channel still alive", and nothing
read it: whether a channel is alive is read from ``tg_raw_messages``,
where every message carries its date in its payload. Both writers go
with the column, and a dump taken before this revision still holds the
dates.

The downgrade restores the column and none of the dates. A channel's
next walk under the older release writes what its newest message says.
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision: str = "0eed912b1e41"
down_revision: str | Sequence[str] | None = "04ec0c882427"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.drop_column("tg_channels", "last_post_at")


def downgrade() -> None:
    op.add_column(
        "tg_channels",
        sa.Column("last_post_at", sa.DateTime(timezone=True), nullable=True),
    )
