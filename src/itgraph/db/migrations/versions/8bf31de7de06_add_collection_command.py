"""add collection command

Revision ID: 8bf31de7de06
Revises: e1c8ca8589dd
Create Date: 2026-07-30 09:05:59.062324

`itgraph add` resolves usernames, so it spends `contacts.resolveUsername`
— the same rationed method `resolve` spends. Filing its rate limits under
`resolve` would keep the method honest and the attribution false, in the
one column whose purpose is telling the two apart. With both commands
drawing on the same daily ceiling, which of them spent it is the question
the table will actually be asked.

Follows `4bb75804d3cd`, which added `metadata` for the same reason and
settled how: additively, and without a downgrade.

"""

from collections.abc import Sequence

from alembic import op

# revision identifiers, used by Alembic.
revision: str = "8bf31de7de06"
down_revision: str | Sequence[str] | None = "e1c8ca8589dd"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """Upgrade schema.

    Adds the value and does nothing else, deliberately. Postgres 16 will
    run `ADD VALUE` inside a transaction block, but it still refuses to
    let the new value be *used* until that transaction commits — so a
    revision that both added `add` and wrote a row with it would fail on
    the write. There is nothing to write here, and there must not be.

    `IF NOT EXISTS` makes a re-run a no-op rather than an error, which is
    what a half-applied upgrade needs.
    """
    op.execute("ALTER TYPE collection_command ADD VALUE IF NOT EXISTS 'add'")


def downgrade() -> None:
    """Downgrade schema — deliberately empty.

    Postgres has no `ALTER TYPE ... DROP VALUE`. Removing a value means
    rebuilding the type and rewriting every column that uses it, which is
    a destructive operation to undo a purely additive one. An unused
    value costs nothing and strands nothing, so the honest downgrade is
    to leave it. Rows written while it existed keep reading correctly;
    only the code that would write new ones goes away.
    """
