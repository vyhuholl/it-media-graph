"""alerts held and without feedback

Revision ID: 4678f31aac4f
Revises: d629dc2e11d0
Create Date: 2026-11-11 12:13:52.064856

Two things the delivery loop needed, in one revision because the same
change makes both.

**`alerts.held_at`**: when the bot set an alert aside for its digest —
raised in quiet hours, or past the day's cap. Before it, the direct path
claimed the oldest outstanding alerts and skipped the ones the digest was
to carry, so ten of them at the head of the queue stopped every direct
send until the morning digest. A held alert is now marked, and the direct
path claims only what is not.

**The verdict table goes.** The buttons that filled it were pressed once
in three months, on the first day, as a test; the operator reads alerts
offline when a threshold is argued about, and asked for the buttons to be
removed. The full dump every migration takes first holds the one row. The
downgrade recreates the table and its type empty, with the bot's grant,
so the code before this revision runs against it.
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision: str = "4678f31aac4f"
down_revision: str | Sequence[str] | None = "d629dc2e11d0"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

BOT = "itgraph_bot"
ALERT_VERDICT = sa.Enum("useful", "boring", name="alert_verdict")


def upgrade() -> None:
    """Upgrade schema."""
    op.add_column(
        "alerts",
        sa.Column("held_at", sa.DateTime(timezone=True), nullable=True),
    )
    # Dropping the table drops every grant on it, the bot's and the
    # reader's included.
    op.drop_table("alert_feedback")
    ALERT_VERDICT.drop(op.get_bind(), checkfirst=True)


def downgrade() -> None:
    """Downgrade schema.

    The table as `722f86caaef4` made it, with the grant `c5a30b71e8d4`
    gave the bot. The reader's comes back through its default privileges.
    """
    op.create_table(
        "alert_feedback",
        sa.Column(
            "alert_id", sa.BigInteger(), autoincrement=False, nullable=False
        ),
        sa.Column("verdict", ALERT_VERDICT, nullable=False),
        sa.Column(
            "given_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.ForeignKeyConstraint(
            ["alert_id"],
            ["alerts.id"],
            name=op.f("fk_alert_feedback_alert_id_alerts"),
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("alert_id", name=op.f("pk_alert_feedback")),
    )
    op.execute(f"GRANT SELECT, INSERT, UPDATE ON alert_feedback TO {BOT}")
    op.drop_column("alerts", "held_at")
