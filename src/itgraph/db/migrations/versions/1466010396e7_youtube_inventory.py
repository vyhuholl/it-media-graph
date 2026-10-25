"""youtube inventory

Revision ID: 1466010396e7
Revises: 0637c6783be7
Create Date: 2026-10-25 15:20:36.365971

The YouTube inventory and the quota ledger every YouTube request books
through. Five tables, all `yt_`-prefixed, none referencing a Telegram
table — YouTube ids are strings, and generalising `tg_id` would mean
migrating the working database under a live bot to gain nothing.

`yt_channels.status` reuses Telegram's `channel_status`: one workflow,
one set of values. So the type is referenced here and never created, and
the downgrade must not drop it. Every other type is YouTube's own and is
created explicitly rather than by `create_table`, because two of them
(`yt_quota_pool`, `yt_command`) are used by two tables each, and a second
implicit `CREATE TYPE` fails.

API data — titles, handles, statistics — has one table, `yt_raw_channels`,
because it may be kept at most 30 days (YouTube API Services Developer
Policies III.E.4) and one table is one place to expire it from.

"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

# revision identifiers, used by Alembic.
revision: str = "1466010396e7"
down_revision: str | Sequence[str] | None = "0637c6783be7"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

YT_CHANNEL_KIND = postgresql.ENUM(
    "personal",
    "company",
    "media",
    "event",
    "education",
    name="yt_channel_kind",
)
YT_REJECT_REASON = postgresql.ENUM(
    "not_it",
    "adjacent",
    "crypto",
    "infobiz",
    "ads",
    "content_farm",
    "consumer_tech",
    name="yt_reject_reason",
)
YT_DISCOVERY_SOURCE = postgresql.ENUM("manual", name="yt_discovery_source")
YT_LANG = postgresql.ENUM("en", "ru", "other", name="yt_lang")
YT_QUOTA_POOL = postgresql.ENUM("general", name="yt_quota_pool")
YT_COMMAND = postgresql.ENUM("add", "refresh", name="yt_command")
YT_REQUEST_OUTCOME = postgresql.ENUM(
    "ok", "not_found", "quota_exceeded", "error", name="yt_request_outcome"
)

NEW_TYPES = (
    YT_CHANNEL_KIND,
    YT_REJECT_REASON,
    YT_DISCOVERY_SOURCE,
    YT_LANG,
    YT_QUOTA_POOL,
    YT_COMMAND,
    YT_REQUEST_OUTCOME,
)

# Telegram's, and already there.
CHANNEL_STATUS = postgresql.ENUM(
    "candidate",
    "seed",
    "maybe",
    "rejected",
    name="channel_status",
    create_type=False,
)


def _column_type(type_: postgresql.ENUM) -> postgresql.ENUM:
    """A type created above, as a column refers to it: without creating it."""
    return postgresql.ENUM(*type_.enums, name=type_.name, create_type=False)


def upgrade() -> None:
    """Upgrade schema."""
    bind = op.get_bind()
    for type_ in NEW_TYPES:
        type_.create(bind)

    op.create_table(
        "yt_channels",
        sa.Column("channel_id", sa.Text(), nullable=False),
        sa.Column(
            "status",
            CHANNEL_STATUS,
            server_default="candidate",
            nullable=False,
        ),
        sa.Column("kind", _column_type(YT_CHANNEL_KIND), nullable=True),
        sa.Column("kind_note", sa.Text(), nullable=True),
        sa.Column(
            "reject_reason", _column_type(YT_REJECT_REASON), nullable=True
        ),
        sa.Column("reject_note", sa.Text(), nullable=True),
        sa.Column("lang", _column_type(YT_LANG), nullable=True),
        sa.Column(
            "discovered_via",
            _column_type(YT_DISCOVERY_SOURCE),
            nullable=False,
        ),
        sa.Column(
            "first_seen_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column("reviewed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("gone_at", sa.DateTime(timezone=True), nullable=True),
        sa.CheckConstraint(
            "(status = 'rejected') = (reject_reason IS NOT NULL)",
            name=op.f("ck_yt_channels_rejected_has_reason"),
        ),
        sa.CheckConstraint(
            "channel_id ~ '^UC[0-9A-Za-z_-]{22}$'",
            name=op.f("ck_yt_channels_id_shape"),
        ),
        sa.CheckConstraint(
            "status <> 'seed' OR (kind IS NOT NULL AND lang IS NOT NULL)",
            name=op.f("ck_yt_channels_seed_is_labelled"),
        ),
        sa.PrimaryKeyConstraint("channel_id", name=op.f("pk_yt_channels")),
    )
    op.create_table(
        "yt_raw_channels",
        sa.Column("channel_id", sa.Text(), nullable=False),
        sa.Column(
            "payload",
            postgresql.JSONB(astext_type=sa.Text()),
            nullable=False,
        ),
        sa.Column("fetched_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(
            ["channel_id"],
            ["yt_channels.channel_id"],
            name=op.f("fk_yt_raw_channels_channel_id_yt_channels"),
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("channel_id", name=op.f("pk_yt_raw_channels")),
    )
    op.create_index(
        "ix_yt_raw_channels_fetched_at",
        "yt_raw_channels",
        ["fetched_at"],
        unique=False,
    )

    op.create_table(
        "yt_quota_days",
        sa.Column("day", sa.Date(), nullable=False),
        sa.Column("pool", _column_type(YT_QUOTA_POOL), nullable=False),
        sa.Column(
            "spent", sa.Integer(), server_default=sa.text("0"), nullable=False
        ),
        sa.CheckConstraint(
            "spent >= 0", name=op.f("ck_yt_quota_days_spent_is_not_negative")
        ),
        sa.PrimaryKeyConstraint("day", "pool", name=op.f("pk_yt_quota_days")),
    )
    op.create_table(
        "yt_quota_exhausted",
        sa.Column("day", sa.Date(), nullable=False),
        sa.Column(
            "observed_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column("command", _column_type(YT_COMMAND), nullable=False),
        sa.PrimaryKeyConstraint("day", name=op.f("pk_yt_quota_exhausted")),
    )
    op.create_table(
        "yt_requests",
        sa.Column("id", sa.BigInteger(), nullable=False),
        sa.Column(
            "requested_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column("day", sa.Date(), nullable=False),
        sa.Column("pool", _column_type(YT_QUOTA_POOL), nullable=False),
        sa.Column("command", _column_type(YT_COMMAND), nullable=False),
        sa.Column("method", sa.Text(), nullable=False),
        sa.Column("units", sa.Integer(), nullable=False),
        sa.Column("outcome", _column_type(YT_REQUEST_OUTCOME), nullable=True),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_yt_requests")),
    )
    op.create_index("ix_yt_requests_day", "yt_requests", ["day"], unique=False)


def downgrade() -> None:
    """Downgrade schema.

    Drops every YouTube table and type. `channel_status` stays: it is
    Telegram's, and `channels` still uses it.
    """
    op.drop_index("ix_yt_requests_day", table_name="yt_requests")
    op.drop_table("yt_requests")
    op.drop_table("yt_quota_exhausted")
    op.drop_table("yt_quota_days")
    op.drop_index(
        "ix_yt_raw_channels_fetched_at", table_name="yt_raw_channels"
    )
    op.drop_table("yt_raw_channels")
    op.drop_table("yt_channels")

    bind = op.get_bind()
    for type_ in reversed(NEW_TYPES):
        type_.drop(bind)
