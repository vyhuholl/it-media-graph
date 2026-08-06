"""spike alert kinds

Revision ID: f39ba7c051de
Revises: 48d4cce98618
Create Date: 2026-08-06 09:18:02.771905

Four values for the four metrics a post can spike on. Separate kinds
rather than one `spike` with a metric column, because they mean different
things — reach, approval, an endorsement strong enough to republish, an
argument — and the kind is what a later query groups by when asking which
of them the operator actually found useful.

Declared here rather than alongside the alert queue, for the reason that
revision gave: a value nothing can raise is a promise made in a type, and
a reader cannot tell it from a feature that quietly stopped working.

"""

from collections.abc import Sequence

from alembic import op

# revision identifiers, used by Alembic.
revision: str = "f39ba7c051de"
down_revision: str | Sequence[str] | None = "48d4cce98618"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

KINDS = ("views_spike", "reaction_spike", "forward_spike", "comment_spike")


def upgrade() -> None:
    """Upgrade schema.

    Adds the values and does nothing else, which is why it is its own
    revision. Postgres will run `ADD VALUE` inside a transaction block
    but refuses to let the new value be *used* until that transaction
    commits, and Alembic wraps a revision in one — so a revision that
    both added a kind and wrote a row carrying it would fail on the
    write. Keeping them apart also means no later edit to the table
    revision can reintroduce that.
    """
    for kind in KINDS:
        op.execute(f"ALTER TYPE alert_kind ADD VALUE IF NOT EXISTS '{kind}'")


def downgrade() -> None:
    """Downgrade schema — deliberately empty.

    Postgres has no `ALTER TYPE ... DROP VALUE`. Removing one means
    rebuilding the type and rewriting every column that uses it, which is
    a destructive operation to undo a purely additive one. An unused
    value costs nothing and strands nothing.
    """
