"""youtube telegram candidate values

Revision ID: b7f8952801f5
Revises: 9a4c6e2f7b31
Create Date: 2026-11-01 12:45:52.009534

The ranking of the Telegram seeds' links adds candidates of its own, so
`yt_discovery_source` gains `telegram`; and the lookups behind it are a
command of their own, so `yt_command` gains `tg-lookup`, which keeps
their spending apart from the others' in the journal.

"""

from collections.abc import Sequence

from alembic import op

# revision identifiers, used by Alembic.
revision: str = "b7f8952801f5"
down_revision: str | Sequence[str] | None = "9a4c6e2f7b31"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """Upgrade schema.

    Adds the values and does nothing else, as `5d2b8e17c4a9` did and for
    its reason: Postgres runs `ADD VALUE` inside a transaction but will
    not let the value be used until that transaction commits, and
    Alembic wraps a revision in one. The lookup table comes in the next
    revision.

    `IF NOT EXISTS` makes a re-run after a half-applied upgrade a no-op.
    """
    op.execute(
        "ALTER TYPE yt_discovery_source ADD VALUE IF NOT EXISTS 'telegram'"
    )
    op.execute("ALTER TYPE yt_command ADD VALUE IF NOT EXISTS 'tg-lookup'")


def downgrade() -> None:
    """Downgrade schema — deliberately empty.

    Postgres has no `ALTER TYPE ... DROP VALUE`. Removing one means
    rebuilding the type and rewriting every column that uses it, which is
    a destructive operation to undo a purely additive one. An unused
    value costs nothing and strands nothing — and a used one belongs to
    records that are never deleted.
    """
