"""youtube backfill values

Revision ID: 5d2b8e17c4a9
Revises: 321e40440221
Create Date: 2026-11-01 14:00:00.000000

The history walk is a command of its own and a way of finding videos of
its own, so `yt_command` and `yt_discovery_path` both gain `backfill`.
The command value is what makes the walk's requests distinguishable in
the journal; the path value keeps the walk's videos out of the poll
loop's count-against-check measurement.

"""

from collections.abc import Sequence

from alembic import op

# revision identifiers, used by Alembic.
revision: str = "5d2b8e17c4a9"
down_revision: str | Sequence[str] | None = "321e40440221"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """Upgrade schema.

    Adds the values and does nothing else, as `b4e1d7c93af2` did and for
    its reason: Postgres runs `ADD VALUE` inside a transaction but will
    not let the value be used until that transaction commits, and
    Alembic wraps a revision in one. The table the walk writes comes in
    the next revision.

    `IF NOT EXISTS` makes a re-run after a half-applied upgrade a no-op.
    """
    op.execute("ALTER TYPE yt_command ADD VALUE IF NOT EXISTS 'backfill'")
    op.execute(
        "ALTER TYPE yt_discovery_path ADD VALUE IF NOT EXISTS 'backfill'"
    )


def downgrade() -> None:
    """Downgrade schema — deliberately empty.

    Postgres has no `ALTER TYPE ... DROP VALUE`. Removing one means
    rebuilding the type and rewriting every column that uses it, which is
    a destructive operation to undo a purely additive one. An unused
    value costs nothing and strands nothing.
    """
