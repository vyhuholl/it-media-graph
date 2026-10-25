"""folded alert delivery

Revision ID: 4a71dacaa963
Revises: 7a1e3c9d40b2
Create Date: 2026-10-25 06:15:20.984276

One value for an alert that was counted inside another's message rather
than sent: a spike folded into the head of its channel's event.

Its own value rather than `direct` because the daily cap counts `direct`
rows, so six folds marked direct would spend six of the day's messages on
the one that actually went out. Rather than `digest` because a digest
line is a message the reader saw, and a fold is a number inside one.

Alone in its revision for the reason `spike alert kinds` gave: Postgres
will not let a value added by `ADD VALUE` be used inside the transaction
that added it.

"""

from collections.abc import Sequence

from alembic import op

# revision identifiers, used by Alembic.
revision: str = "4a71dacaa963"
down_revision: str | Sequence[str] | None = "7a1e3c9d40b2"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """Upgrade schema."""
    op.execute("ALTER TYPE alert_delivery ADD VALUE IF NOT EXISTS 'folded'")


def downgrade() -> None:
    """Downgrade schema — deliberately empty.

    Postgres has no `ALTER TYPE ... DROP VALUE`, and rebuilding the type
    to undo an additive change is a destructive operation for no gain.
    An unused value strands nothing.
    """
