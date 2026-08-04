"""bot database role

Revision ID: c5a30b71e8d4
Revises: 722f86caaef4
Create Date: 2026-08-04 00:11:38.902114

What the bot may touch, enforced by the database rather than by
convention. The bot's token is the one credential in this project that
plausibly ends up on a machine the operator does not own, and the
difference between "the bot does not write collection state" and "the bot
cannot" is the difference between a comment and a guarantee.

**This revision creates no password**, and that is deliberate rather than
an omission. A password in a migration is a committed secret, which is the
one thing this project refuses outright — so the role is created without
one and the operator sets it, out of band, once. See the bot section of
`src/itgraph/README.md`.

The grants are one-directional on purpose. The bot reads what it renders
from and writes only the two alert tables; it cannot write the inventory,
the raw layer, the snapshots, the edges or any collection cursor. It can
still *read* post text and channel titles, which is the residual risk and
the reason to keep the bot on the operator's own machine until there is a
reason not to.

"""

from collections.abc import Sequence

from alembic import op

# revision identifiers, used by Alembic.
revision: str = "c5a30b71e8d4"
down_revision: str | Sequence[str] | None = "722f86caaef4"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

ROLE = "itgraph_bot"

# Everything the rendering path reads. Deliberately enumerated rather
# than granted schema-wide: a table added later should have to be named
# here to become readable by the bot, not become readable by default.
READABLE = (
    "alerts",
    "alert_feedback",
    "channels",
    "raw_messages",
    "edges",
    "channel_families",
)

WRITABLE = ("alerts", "alert_feedback")


def upgrade() -> None:
    """Upgrade schema.

    `CREATE ROLE` has no `IF NOT EXISTS` in Postgres, hence the `DO`
    block: a re-run must be a no-op rather than an error, because a
    half-applied upgrade is exactly when this gets run twice.

    `NOLOGIN` until a password is set, so the role cannot be connected as
    before somebody deliberately makes it possible.
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

    # `GRANT ... ON DATABASE` takes a literal name and refuses
    # `CURRENT_CATALOG`, so the name is resolved at run time and quoted
    # by `format`'s `%I`. Hardcoding it would tie this revision to one
    # database and break every scratch verification.
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
    for table in WRITABLE:
        op.execute(f"GRANT INSERT, UPDATE ON {table} TO {ROLE}")

    # `alerts.id` is a generated identity, and inserting into it needs the
    # sequence. Named explicitly rather than granted across the schema,
    # for the same reason the tables are.
    op.execute(f"GRANT USAGE ON SEQUENCE alerts_id_seq TO {ROLE}")


def downgrade() -> None:
    """Downgrade schema.

    Revokes and drops the role. `DROP ROLE` fails while any grant still
    references it, so the revokes are not decoration — and the role may
    own nothing else, which is why it was created with no other
    privileges in the first place.
    """
    for table in WRITABLE:
        op.execute(f"REVOKE INSERT, UPDATE ON {table} FROM {ROLE}")
    for table in READABLE:
        op.execute(f"REVOKE SELECT ON {table} FROM {ROLE}")
    op.execute(f"REVOKE USAGE ON SEQUENCE alerts_id_seq FROM {ROLE}")
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
    op.execute(f"DROP ROLE IF EXISTS {ROLE}")
