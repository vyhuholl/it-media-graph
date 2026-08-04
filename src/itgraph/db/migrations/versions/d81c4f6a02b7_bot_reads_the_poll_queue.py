"""bot reads the poll queue

Revision ID: d81c4f6a02b7
Revises: c5a30b71e8d4
Create Date: 2026-08-04 21:44:12.336017

Fixes an omission in the grants of `c5a30b71e8d4`, which enumerated the
tables the *rendering* path reads and forgot that `/status` is not the
rendering path. It reports how far the poll queue is behind — the number
that separates "nothing has travelled" from "collection stopped on
Tuesday" — and that comes from `poll_state` joined to `backfill_state`.

The result would have been a bot that delivered alerts correctly and
answered `/status` with a permission error: broken in the one command
whose entire job is to say whether anything is broken.

Reading collection state is not what the role exists to prevent. The
guarantee is that the bot cannot *write* it, and that is unchanged —
these are `SELECT` and nothing more. The role already reads `channels`,
`raw_messages` and `edges`, which carry considerably more than a due
timestamp and a cursor.

"""

from collections.abc import Sequence

from alembic import op

# revision identifiers, used by Alembic.
revision: str = "d81c4f6a02b7"
down_revision: str | Sequence[str] | None = "c5a30b71e8d4"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

ROLE = "itgraph_bot"
TABLES = ("poll_state", "backfill_state")


def upgrade() -> None:
    """Upgrade schema."""
    for table in TABLES:
        op.execute(f"GRANT SELECT ON {table} TO {ROLE}")


def downgrade() -> None:
    """Downgrade schema."""
    for table in TABLES:
        op.execute(f"REVOKE SELECT ON {table} FROM {ROLE}")
