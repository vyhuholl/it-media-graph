"""yt offer command

Revision ID: 24a189012c7b
Revises: 25a92a53c2ec
Create Date: 2026-11-21 10:12:00.000000

One value for the rising selection's one call a day: the payloads of
the channels it offers, bought the moment the day is computed so their
cards can be titled the day they are sent. A value of its own for the
same reason the enum exists at all — a row under `offer` says what
spent the unit, not a `refresh` that did not run.

Added here and nowhere else, for the reason the spike kinds' revision
gave: Postgres runs `ADD VALUE` inside a transaction block but refuses
to let the new value be *used* until that transaction commits, and
Alembic wraps a revision in one.

"""

from collections.abc import Sequence

from alembic import op

# revision identifiers, used by Alembic.
revision: str = "24a189012c7b"
down_revision: str | Sequence[str] | None = "25a92a53c2ec"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """Upgrade schema."""
    op.execute("ALTER TYPE yt_command ADD VALUE IF NOT EXISTS 'offer'")


def downgrade() -> None:
    """Downgrade schema — deliberately empty.

    Postgres has no `ALTER TYPE ... DROP VALUE`. Removing one means
    rebuilding the type and rewriting every column that uses it, which
    is a destructive operation to undo a purely additive one. An unused
    value costs nothing and strands nothing.
    """
