"""daily summary markers

Revision ID: 25a92a53c2ec
Revises: fe648a4ec45e
Create Date: 2026-11-19 07:07:25.020607

The daily summaries' markers: one row per destination and day, saying
whether the day's recap has gone out whole.

**The day and its row.** `destination` is the destination's constant key
— `telegram`, `youtube_en`, `youtube_ru` — and `day` the label date the
summary names («Сводка за 18.11.2026»): the quiet window's complement
in the destination's own zone, worked out at sending. A row is opened
when the day's summary begins going out and closed (`sent_at`) once
every part has; an open row is a summary interrupted part-way, which
the next pass resends whole, and which the restore closes
(`files/neutralise.sql`) so that no copy finishes a day production left
half-sent. A quiet day leaves no row.

**The primary key is the whole point.** Unique `(destination, day)` is
what makes the summary restart-safe without bot memory, and it is the
only index: the bot asks for one row a day.

**The grants.** The write is the bot's alone — there is no pass here,
the bot is both producer and consumer — so it takes the table whole for
insert and reading, and `sent_at` alone for update: opening a row and
closing it are all it does. The reader takes the table through its
default privileges; the series take nothing, as the digest's do not.
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision: str = "25a92a53c2ec"
down_revision: str | Sequence[str] | None = "fe648a4ec45e"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

BOT = "itgraph_bot"


def upgrade() -> None:
    """Upgrade schema."""
    op.create_table(
        "bot_daily_summaries",
        sa.Column("destination", sa.Text(), nullable=False),
        sa.Column("day", sa.Date(), nullable=False),
        sa.Column("sent_at", sa.DateTime(timezone=True), nullable=True),
        sa.PrimaryKeyConstraint(
            "destination", "day", name=op.f("pk_bot_daily_summaries")
        ),
    )
    op.execute(f"GRANT SELECT, INSERT ON bot_daily_summaries TO {BOT}")
    op.execute(f"GRANT UPDATE (sent_at) ON bot_daily_summaries TO {BOT}")


def downgrade() -> None:
    """Downgrade schema.

    The grants go with the table.
    """
    op.drop_table("bot_daily_summaries")
