"""alerts folded into a head

Revision ID: 7fe57a58ffaf
Revises: 4a71dacaa963
Create Date: 2026-10-25 06:15:22.394515

`alerts.folded_into`: the head a spike was told through. When a whole
channel's audience grows at once, every recent post crosses the threshold
together; the first becomes the head and the rest point at it here.

A fold is a row rather than an absence because the unique constraint is
what stops a post being raised twice. A post folded at 18:00 is still
inside the scoring window when the fold window closes, and without its
row the next pass would raise it as a new head.

Nothing is backfilled. Every existing alert is a message of its own, which
is exactly what a null here says.

No grant changes: the bot's role holds table-level `SELECT, UPDATE` on
`alerts`, which covers the new column, and it only ever reads this one.

"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision: str = "7fe57a58ffaf"
down_revision: str | Sequence[str] | None = "4a71dacaa963"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """Upgrade schema."""
    op.add_column(
        "alerts", sa.Column("folded_into", sa.BigInteger(), nullable=True)
    )
    op.create_foreign_key(
        op.f("fk_alerts_folded_into_alerts"),
        "alerts",
        "alerts",
        ["folded_into"],
        ["id"],
        ondelete="CASCADE",
    )
    op.create_check_constraint(
        op.f("ck_alerts_not_folded_into_itself"),
        "alerts",
        "folded_into <> id",
    )
    op.create_index(
        "ix_alerts_folded_into",
        "alerts",
        ["folded_into"],
        unique=False,
        postgresql_where=sa.text("folded_into IS NOT NULL"),
    )


def downgrade() -> None:
    """Downgrade schema.

    Drops the column and what hangs off it. Without the column a fold
    still waiting for its head is an ordinary undelivered alert, and the
    bot would send each of them — the series this column exists to
    prevent, arriving all at once. So those are settled first, as their
    head would have settled them.
    """
    op.execute(
        "UPDATE alerts SET delivered_at = now(), delivery = 'folded' "
        "WHERE folded_into IS NOT NULL AND delivered_at IS NULL"
    )
    op.drop_index(
        "ix_alerts_folded_into",
        table_name="alerts",
        postgresql_where=sa.text("folded_into IS NOT NULL"),
    )
    op.drop_constraint(
        op.f("ck_alerts_not_folded_into_itself"), "alerts", type_="check"
    )
    op.drop_constraint(
        op.f("fk_alerts_folded_into_alerts"), "alerts", type_="foreignkey"
    )
    op.drop_column("alerts", "folded_into")
