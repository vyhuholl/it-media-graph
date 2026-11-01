"""youtube discovery values

Revision ID: f25cff672b9d
Revises: 5b0d70841f06
Create Date: 2026-11-01 16:23:55.247131

YouTube's own sources of candidates add theirs to `yt_discovery_source`
— `mention`, `featured`, `search` — so the yield of each is read off the
inventory. The lookups of YouTube's references and the search are
commands of their own in the journal (`yt_command`), and search spends a
pool of its own (`yt_quota_pool`), outside the daily units.

"""

from collections.abc import Sequence

from alembic import op

# revision identifiers, used by Alembic.
revision: str = "f25cff672b9d"
down_revision: str | Sequence[str] | None = "5b0d70841f06"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """Upgrade schema.

    Adds the values and does nothing else, as `5d2b8e17c4a9` did and for
    its reason: Postgres runs `ADD VALUE` inside a transaction but will
    not let the value be used until that transaction commits, and
    Alembic wraps a revision in one. The tables come in the next
    revision.

    `IF NOT EXISTS` makes a re-run after a half-applied upgrade a no-op.
    """
    for value in ("mention", "featured", "search"):
        op.execute(
            f"ALTER TYPE yt_discovery_source ADD VALUE IF NOT EXISTS '{value}'"
        )
    for value in ("mention-lookup", "search"):
        op.execute(f"ALTER TYPE yt_command ADD VALUE IF NOT EXISTS '{value}'")
    op.execute("ALTER TYPE yt_quota_pool ADD VALUE IF NOT EXISTS 'search'")


def downgrade() -> None:
    """Downgrade schema — deliberately empty.

    Postgres has no `ALTER TYPE ... DROP VALUE`. Removing one means
    rebuilding the type and rewriting every column that uses it, which is
    a destructive operation to undo a purely additive one. An unused
    value costs nothing and strands nothing — and a used one belongs to
    records that are never deleted.
    """
