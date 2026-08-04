"""alert queue

Revision ID: 722f86caaef4
Revises: b4e1d7c93af2
Create Date: 2026-08-04 00:04:02.657425

The interface between detection and delivery. A pass writes rows into
`alerts`, a bot reads them and records what the operator thought — and
that table is the whole contract, so either side can move to another
machine without the other changing.

Additive: nothing existing changes shape, so the downgrade is a clean
drop. It has one thing autogenerate does not do, and the comment on it is
there because it is a silent failure rather than a loud one.

"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision: str = "722f86caaef4"
down_revision: str | Sequence[str] | None = "b4e1d7c93af2"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

# Named here rather than inline, because the downgrade has to drop them
# by name and the two spellings must not be able to drift.
ALERT_KIND = sa.Enum("repost_cascade", name="alert_kind")
ALERT_DELIVERY = sa.Enum("direct", "digest", name="alert_delivery")
ALERT_VERDICT = sa.Enum("useful", "boring", name="alert_verdict")


def upgrade() -> None:
    """Upgrade schema."""
    op.create_table(
        "alerts",
        sa.Column("id", sa.BigInteger(), nullable=False),
        sa.Column("kind", ALERT_KIND, nullable=False),
        sa.Column("channel_id", sa.BigInteger(), nullable=False),
        sa.Column("msg_id", sa.BigInteger(), nullable=False),
        sa.Column("band", sa.Integer(), nullable=False),
        sa.Column("value", sa.Float(), nullable=False),
        sa.Column(
            "raised_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column("delivered_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("delivery", ALERT_DELIVERY, nullable=True),
        sa.Column(
            "attempts",
            sa.Integer(),
            server_default=sa.text("0"),
            nullable=False,
        ),
        sa.Column("last_error", sa.Text(), nullable=True),
        # An alert about a post nothing collected must not be
        # representable. The composite key is `raw_messages`' primary
        # key, so this costs no index of its own.
        sa.ForeignKeyConstraint(
            ["channel_id", "msg_id"],
            ["raw_messages.channel_id", "raw_messages.msg_id"],
            name=op.f("fk_alerts_channel_id_raw_messages"),
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_alerts")),
        # This constraint *is* the escalation logic: a post at band 2
        # raises one row, at band 3 a second, and standing still raises
        # nothing. Nothing beside it needs to remember what was said.
        sa.UniqueConstraint(
            "kind", "channel_id", "msg_id", "band", name="uq_alerts_post_band"
        ),
    )
    # Partial: the bot asks "what is outstanding" on every tick forever,
    # and the answer is a handful of rows out of everything ever raised.
    op.create_index(
        "ix_alerts_undelivered",
        "alerts",
        ["raised_at"],
        unique=False,
        postgresql_where=sa.text("delivered_at IS NULL"),
    )

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


def downgrade() -> None:
    """Downgrade schema.

    The enum types are dropped by hand, and that is the part autogenerate
    leaves out. Postgres does not remove a type when the table using it
    goes, so a downgrade followed by an upgrade would fail on `type
    "alert_kind" already exists` — a failure that only appears when
    somebody exercises the downgrade, which is exactly when they are
    least expecting the migration itself to be the problem.

    Unlike the stranded `watch` enum *value* in an earlier revision,
    these are whole types created by this revision, so dropping them is
    symmetric rather than destructive.
    """
    op.drop_table("alert_feedback")
    op.drop_index(
        "ix_alerts_undelivered",
        table_name="alerts",
        postgresql_where=sa.text("delivered_at IS NULL"),
    )
    op.drop_table("alerts")

    bind = op.get_bind()
    ALERT_VERDICT.drop(bind, checkfirst=True)
    ALERT_DELIVERY.drop(bind, checkfirst=True)
    ALERT_KIND.drop(bind, checkfirst=True)
