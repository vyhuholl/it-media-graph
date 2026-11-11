"""reader database role

Revision ID: d629dc2e11d0
Revises: ea870c053ba7
Create Date: 2026-11-11 07:59:35.683332

An operator's door to the database, and no workload's. Through the SSH
tunnel to the fixed address the operators read production, and until now
the only role there that could read everything was the owner, which can
also change everything. This role reads every table and changes none.

It is granted schema-wide, where the bot's revision enumerates on
purpose. That reasoning is about a workload whose credential could leak
to a machine the operator does not own, and a table added later should
have to be named before such a role reads it. This role's rights are a
subset of the owner's, which operators held through the tunnel before it
existed: it narrows what a session there can do, and widens nothing. So
a table a later migration creates is readable with no migration of its
own, through the default privileges of the role that runs migrations.

Its sessions start guarded, and **the guards are not a boundary**: a
session may change any setting, and only the connection limit holds
regardless. They are there against accidents, which on this database
have already cost a crash recovery, from a read-only query's memory. The
boundary is the grants: no privilege to change data, whatever a session
does with its settings.

**This revision creates no password**, for the reason the bot's role
gives: a password in a migration is a committed secret. The deploy sets
one from its input, or leaves the role unable to log in.
"""

from collections.abc import Sequence

from alembic import op

# revision identifiers, used by Alembic.
revision: str = "d629dc2e11d0"
down_revision: str | Sequence[str] | None = "ea870c053ba7"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

ROLE = "itgraph_reader"

# Enough for an operator and an agent at once, and few enough that a tool
# opening a pool cannot take the database's connections.
CONNECTION_LIMIT = 4

# What each session of the role starts with. Read-only catches a stray
# write and refuses the one the grants leave open, a temporary table
# (`PUBLIC` holds `TEMP`). `work_mem` bounds a sort or a hash per node,
# since memory and not time is what crashed the database. The timeouts end
# a query left running from a laptop, and a transaction left open before
# its lock holds up a deploy's migration and everything queued behind it.
GUARDS = {
    "default_transaction_read_only": "on",
    "work_mem": "'16MB'",
    "statement_timeout": "'10min'",
    "idle_in_transaction_session_timeout": "'5min'",
}


def upgrade() -> None:
    """Upgrade schema.

    `NOLOGIN` until the deploy sets a password. A `DO` block, because
    `CREATE ROLE` has no `IF NOT EXISTS` and a role outlives any one
    database of the cluster; every other statement here is a no-op when
    run again.

    The default privileges name no role, so they are the current role's:
    whoever runs the migrations, and so creates the tables.
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
    op.execute(f"GRANT SELECT ON ALL TABLES IN SCHEMA public TO {ROLE}")
    op.execute(
        "ALTER DEFAULT PRIVILEGES IN SCHEMA public "
        f"GRANT SELECT ON TABLES TO {ROLE}"
    )

    op.execute(f"ALTER ROLE {ROLE} CONNECTION LIMIT {CONNECTION_LIMIT}")
    for name, value in GUARDS.items():
        op.execute(f"ALTER ROLE {ROLE} SET {name} = {value}")


def downgrade() -> None:
    """Downgrade schema.

    Everything granted in this database is revoked, the default privileges
    first: they are what a `DROP ROLE` most often fails on. `CONNECT` is
    revoked in a block of its own, so that the drop's failure, which rolls
    back its block, does not keep it.

    The role is dropped only if nothing else in the cluster still names
    it. A role belongs to the cluster, and a scratch database is verified
    on the same instance as the working one, whose grants must not make
    this downgrade fail, let alone be touched by it. A kept role keeps its
    settings, which belong to it and not to this database.
    """
    op.execute(
        "ALTER DEFAULT PRIVILEGES IN SCHEMA public "
        f"REVOKE SELECT ON TABLES FROM {ROLE}"
    )
    op.execute(f"REVOKE SELECT ON ALL TABLES IN SCHEMA public FROM {ROLE}")
    op.execute(f"REVOKE USAGE ON SCHEMA public FROM {ROLE}")
    op.execute(
        f"""
        DO $$
        BEGIN
            EXECUTE format(
                'REVOKE CONNECT ON DATABASE %I FROM {ROLE}',
                current_database()
            );
        END
        $$;
        """
    )
    op.execute(
        f"""
        DO $$
        BEGIN
            DROP ROLE IF EXISTS {ROLE};
        EXCEPTION
            WHEN dependent_objects_still_exist THEN
                RAISE NOTICE 'role {ROLE} is still used elsewhere; kept';
        END
        $$;
        """
    )
