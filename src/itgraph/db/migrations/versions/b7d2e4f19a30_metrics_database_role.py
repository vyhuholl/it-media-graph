"""metrics database role

Revision ID: b7d2e4f19a30
Revises: a89cb273f885
Create Date: 2026-11-06 12:00:00.000000

What the exporter may read, enforced by the database. It serves numbers
to a monitoring store, so it has no business holding anything it could
leak: not a title, not a handle, not a post, not which channels came from
the operator's own subscriptions.

So the grants are by column wherever a table holds anything else. Of the
YouTube tables it sees only when each row was fetched. Of the inventory
it sees the three columns that say a channel is in scope.

**This revision creates no password**, for the reason the bot's role gives:
a password in a migration is a committed secret. The deploy sets it.
"""

from collections.abc import Sequence

from alembic import op

# revision identifiers, used by Alembic.
revision: str = "b7d2e4f19a30"
down_revision: str | Sequence[str] | None = "a89cb273f885"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

ROLE = "itgraph_metrics"

# Whole tables, which hold nothing but the project's own bookkeeping.
READABLE = (
    "poll_state",
    "flood_events",
    "yt_quota_days",
    "yt_quota_exhausted",
    "yt_watch_passes",
    "alerts",
)

# One or a few columns of a table that holds more.
READABLE_COLUMNS = {
    "channels": ("tg_id", "status", "is_chat"),
    "backfill_state": ("channel_id", "failure_kind"),
    "yt_raw_channels": ("fetched_at",),
    "yt_raw_videos": ("fetched_at",),
    "yt_raw_video_stats": ("observed_at",),
    "yt_raw_lookups": ("fetched_at",),
    "yt_raw_channel_sections": ("fetched_at",),
    "yt_search_runs": ("asked_at",),
}


def upgrade() -> None:
    """Upgrade schema.

    `NOLOGIN` until the deploy sets a password, as the bot's role is. A
    `DO` block, because `CREATE ROLE` has no `IF NOT EXISTS` and a role
    outlives any one database of the cluster.
    """
    op.execute(
        f"""
        DO $$
        BEGIN
            IF NOT EXISTS (
                SELECT 1 FROM pg_roles WHERE rolname = '{ROLE}'
            ) THEN
                CREATE ROLE {ROLE} NOLOGIN;
            END IF;
        END
        $$;
        """
    )
    op.execute(
        f"""
        DO $$
        BEGIN
            EXECUTE format(
                'GRANT CONNECT ON DATABASE %I TO {ROLE}', current_database()
            );
        END
        $$;
        """
    )
    op.execute(f"GRANT USAGE ON SCHEMA public TO {ROLE}")

    for table in READABLE:
        op.execute(f"GRANT SELECT ON {table} TO {ROLE}")
    for table, columns in READABLE_COLUMNS.items():
        op.execute(f"GRANT SELECT ({', '.join(columns)}) ON {table} TO {ROLE}")


def downgrade() -> None:
    """Downgrade schema.

    Revokes here, and drops the role only if nothing else in the cluster
    still names it. A role belongs to the cluster, and a scratch database
    is verified on the same instance as the working one, whose grants
    must not make this downgrade fail, let alone be touched by it.
    """
    for table, columns in READABLE_COLUMNS.items():
        op.execute(
            f"REVOKE SELECT ({', '.join(columns)}) ON {table} FROM {ROLE}"
        )
    for table in READABLE:
        op.execute(f"REVOKE SELECT ON {table} FROM {ROLE}")
    op.execute(f"REVOKE USAGE ON SCHEMA public FROM {ROLE}")
    op.execute(
        f"""
        DO $$
        BEGIN
            EXECUTE format(
                'REVOKE CONNECT ON DATABASE %I FROM {ROLE}',
                current_database()
            );
            DROP ROLE IF EXISTS {ROLE};
        EXCEPTION
            WHEN dependent_objects_still_exist THEN
                RAISE NOTICE 'role {ROLE} is still used elsewhere; kept';
        END
        $$;
        """
    )
