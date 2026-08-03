"""watch collection command

Revision ID: b4e1d7c93af2
Revises: 67382317442f
Create Date: 2026-08-03 18:22:40.118904

The poll loop is a command of its own and needs a value of its own, for
the reason every other one does: the column exists so that a method
appearing under a command with no business issuing it reads as a
regression. `watch` is the case where that matters most — it is allowed
to spend no quota-bearing request at all, and unlike a backfill it runs
indefinitely, so a leak there would spend the day's quota every day
rather than once.

"""

from collections.abc import Sequence

from alembic import op

# revision identifiers, used by Alembic.
revision: str = "b4e1d7c93af2"
down_revision: str | Sequence[str] | None = "67382317442f"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """Upgrade schema.

    Adds the value and does nothing else, deliberately — the same shape
    as the revision that added `metadata`, and for the same reason.
    Postgres will run `ADD VALUE` inside a transaction block but still
    refuses to let the new value be *used* until that transaction
    commits, and Alembic wraps a revision in one. So a revision that both
    added `watch` and wrote a row carrying it would fail on the write.
    There is nothing to write here, and there must not be.

    This is also why it is a separate revision from the two tables rather
    than a hand-edited addition to that one: keeping them apart means no
    later edit to the table revision can accidentally introduce a write
    that this constraint forbids.

    `IF NOT EXISTS` makes a re-run a no-op rather than an error, which is
    what a half-applied upgrade needs.
    """
    op.execute("ALTER TYPE collection_command ADD VALUE IF NOT EXISTS 'watch'")


def downgrade() -> None:
    """Downgrade schema — deliberately empty.

    Postgres has no `ALTER TYPE ... DROP VALUE`. Removing a value means
    rebuilding the type and rewriting every column that uses it, which is
    a destructive operation to undo a purely additive one. An unused
    value costs nothing and strands nothing, so the honest downgrade is
    to leave it. Rows written while it existed keep reading correctly;
    only the code that would write new ones goes away.
    """
