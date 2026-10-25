"""recent channel references

Revision ID: 0637c6783be7
Revises: f0d81cd6b898
Create Date: 2026-10-25 07:17:36.048175

`channel_baselines.reference`: whether a channel's baseline is the median
of its own newest posts at eight hours, or of its settled posts — the
mature median, which the kind's factor scales to eight hours and which is
only as recent as the history it came from.

Every existing row is a mature median, so the column is filled with
`mature` — that is what they are, not a default claimed for them — and
the server default is then dropped, so a row written from here on has to
say which it is.

`baseline_runs` gains the three parameters a recent reference is taken
under, nullable for the reason `named handle signal` gave: a run made
before them never ran under them.

Creating a type and using it in the same transaction is allowed; the
restriction `spike alert kinds` worked around is on `ADD VALUE` to an
existing one.

"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision: str = "0637c6783be7"
down_revision: str | Sequence[str] | None = "f0d81cd6b898"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

BASELINE_REFERENCE = sa.Enum("recent", "mature", name="baseline_reference")


def upgrade() -> None:
    """Upgrade schema."""
    BASELINE_REFERENCE.create(op.get_bind())
    op.add_column(
        "channel_baselines",
        sa.Column(
            "reference",
            BASELINE_REFERENCE,
            nullable=False,
            server_default="mature",
        ),
    )
    op.alter_column("channel_baselines", "reference", server_default=None)

    for column in ("recent_posts", "recent_min_posts", "recent_max_days"):
        op.add_column(
            "baseline_runs", sa.Column(column, sa.Integer(), nullable=True)
        )


def downgrade() -> None:
    """Downgrade schema.

    Without the column a `recent` row reads as a mature median, and the
    old code would scale it by the factor a second time — an expectation
    at half its value, and a flood. So every run holding one is made
    invisible first, the way a refresh that died half way is: readers
    fall back to the newest run made without them, which is exactly what
    the old code computes anyway, since its medians do not move. The rows
    stay, for whoever wants to compare.
    """
    op.execute(
        "UPDATE baseline_runs SET completed_at = NULL WHERE id IN "
        "(SELECT DISTINCT run_id FROM channel_baselines "
        "WHERE reference = 'recent')"
    )
    for column in ("recent_max_days", "recent_min_posts", "recent_posts"):
        op.drop_column("baseline_runs", column)
    op.drop_column("channel_baselines", "reference")
    BASELINE_REFERENCE.drop(op.get_bind())
