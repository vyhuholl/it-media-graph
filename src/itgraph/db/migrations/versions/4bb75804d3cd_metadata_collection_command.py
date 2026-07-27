"""metadata collection command

Revision ID: 4bb75804d3cd
Revises: 6cd4607f7d9b
Create Date: 2026-07-27 16:08:08.167219

The metadata pass became a command of its own, so its rate limits need a
value of their own. Filing them under `backfill` would defeat the column:
the point of recording which command spent a quota is that a
`ResolveUsernameRequest` appearing under `backfill` is a regression, and
that only reads as one while each command owns its method.

"""

from collections.abc import Sequence

from alembic import op

# revision identifiers, used by Alembic.
revision: str = "4bb75804d3cd"
down_revision: str | Sequence[str] | None = "6cd4607f7d9b"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """Upgrade schema.

    Adds the value and does nothing else, deliberately. Postgres 16 will
    run `ADD VALUE` inside a transaction block — 11 and earlier would
    not — but it still refuses to let the new value be *used* until that
    transaction commits. Alembic wraps a revision in one, so a revision
    that both added `metadata` and wrote a row with it would fail on the
    write. There is nothing to write here, and there must not be.

    `IF NOT EXISTS` makes a re-run a no-op rather than an error, which is
    what a half-applied upgrade needs.
    """
    op.execute(
        "ALTER TYPE collection_command ADD VALUE IF NOT EXISTS 'metadata'"
    )


def downgrade() -> None:
    """Downgrade schema — deliberately empty.

    Postgres has no `ALTER TYPE ... DROP VALUE`. Removing a value means
    rebuilding the type and rewriting every column that uses it, which is
    a destructive operation to undo a purely additive one. An unused
    value costs nothing and strands nothing, so the honest downgrade is
    to leave it. Rows written while it existed keep reading correctly;
    only the code that would write new ones goes away.
    """
