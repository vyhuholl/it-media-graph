"""youtube watch values

Revision ID: eccc180aac8e
Revises: 1466010396e7
Create Date: 2026-10-26 01:17:08.923875

The YouTube poll loop is a command of its own and spends from a pool of
its own, so both types gain `watch`. The command value is what makes a
method booked under the loop distinguishable in the journal; the pool
value is the reserve change 1 held out of the general cap, now with
someone to spend it.

"""

from collections.abc import Sequence

from alembic import op

# revision identifiers, used by Alembic.
revision: str = "eccc180aac8e"
down_revision: str | Sequence[str] | None = "1466010396e7"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """Upgrade schema.

    Adds the values and does nothing else, as `b4e1d7c93af2` did and for
    its reason: Postgres runs `ADD VALUE` inside a transaction but will
    not let the value be used until that transaction commits, and
    Alembic wraps a revision in one. The tables that use these values
    come in the next revision.

    `IF NOT EXISTS` makes a re-run after a half-applied upgrade a no-op.
    """
    op.execute("ALTER TYPE yt_quota_pool ADD VALUE IF NOT EXISTS 'watch'")
    op.execute("ALTER TYPE yt_command ADD VALUE IF NOT EXISTS 'watch'")


def downgrade() -> None:
    """Downgrade schema — deliberately empty.

    Postgres has no `ALTER TYPE ... DROP VALUE`. Removing one means
    rebuilding the type and rewriting every column that uses it, which is
    a destructive operation to undo a purely additive one. An unused
    value costs nothing and strands nothing.
    """
