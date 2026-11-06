"""clear people from the raw layer

Revision ID: fb31bd0b6656
Revises: b7d2e4f19a30
Create Date: 2026-11-06 23:30:00.000000

The collector stored Telegram's objects verbatim, and with them people who
never chose to be in this database: on 2026-11-06, 109,480 of 274,486
posts named someone — 62,167 distinct users among a post's latest
commenters, 6,341 among its top paid reactors. Nothing read any of it.
From this release the collector removes them before storing
(`tg/payload.py`, `without_people`); this revision removes them once from
what was stored before, by the same rule, then rewrites both tables on
disk so their files no longer hold the old values.

**The rule here is a frozen copy**, in PL/pgSQL, of what `without_people`
did when this revision was written. A migration must not change what it
does after it has run somewhere, while the collector's rule may grow; a
rule that grows needs a revision of its own for what is already stored,
and `tests/test_people_migration.py` holds the newest revision's copy
equal to the collector's, so the suite says so.

**The update streams, one row at a time.** A recursive walk over every
payload ran the database out of its 2 GiB on 2026-11-06; this is one
`UPDATE` per table, prefiltered by a JSON path, measured at production's
size before it ran there.

**The downgrade does nothing:** what this removed cannot come back, and
nothing needs it. The full dump taken before an upgrade still holds it,
and is pruned by age like every other.
"""

from collections.abc import Sequence

from alembic import op

# revision identifiers, used by Alembic.
revision: str = "fb31bd0b6656"
down_revision: str | Sequence[str] | None = "b7d2e4f19a30"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

TABLES = ("raw_messages", "raw_channels")

# Whether a payload holds anything the rule removes, anywhere. The
# prefilter of the update, so only rows that change are rewritten; the
# restore's read-back asks the same questions one by one.
NAMES_SOMEONE = (
    "$.** ? ("
    '@."_" == "PeerUser" || @."_" == "InputPeerUser"'
    ' || @."_" == "InputPeerUserFromMessage" || @."_" == "InputUser"'
    ' || @."_" == "InputUserFromMessage" || @."_" == "UserEmpty"'
    ' || (@."_" == "User" && !exists(@.bot ? (@ == true)))'
    ' || @."_" == "MessageEntityMentionName"'
    ' || @."_" == "InputMessageEntityMentionName"'
    " || @.post_author != null || @.from_name != null"
    " || @.saved_from_name != null"
    ' || (@.winners.type() == "array" && exists(@.winners[0]))'
    ' || (@.recent_requesters.type() == "array"'
    " && exists(@.recent_requesters[0]))"
    ")"
)

# The rule, as `without_people` had it. In `pg_temp`, so it lasts for this
# connection alone and leaves nothing in the schema.
FUNCTIONS = (
    """
    CREATE FUNCTION pg_temp.itgraph_is_user(j jsonb) RETURNS boolean
    LANGUAGE sql IMMUTABLE AS $f$
      SELECT coalesce(
        jsonb_typeof(j) = 'object' AND (
          j->>'_' IN ('PeerUser', 'InputPeerUser', 'InputPeerUserFromMessage',
                      'InputUser', 'InputUserFromMessage', 'UserEmpty')
          OR (j->>'_' = 'User' AND j->'bot' IS DISTINCT FROM 'true'::jsonb)
        ),
        false)
    $f$
    """,
    """
    CREATE FUNCTION pg_temp.itgraph_without_people(j jsonb) RETURNS jsonb
    LANGUAGE plpgsql IMMUTABLE AS $f$
    DECLARE
      k text;
      v jsonb;
      e jsonb;
      cleared jsonb;
    BEGIN
      IF jsonb_typeof(j) = 'object' THEN
        IF pg_temp.itgraph_is_user(j) THEN
          RETURN 'null'::jsonb;
        END IF;
        cleared := '{}'::jsonb;
        FOR k, v IN SELECT key, value FROM jsonb_each(j) LOOP
          IF k IN ('post_author', 'from_name', 'saved_from_name')
             AND v <> 'null'::jsonb THEN
            cleared := cleared || jsonb_build_object(k, 'null'::jsonb);
          ELSIF k IN ('winners', 'recent_requesters')
                AND jsonb_typeof(v) = 'array' THEN
            cleared := cleared || jsonb_build_object(k, '[]'::jsonb);
          ELSE
            cleared := cleared
              || jsonb_build_object(k, pg_temp.itgraph_without_people(v));
          END IF;
        END LOOP;
        RETURN cleared;
      ELSIF jsonb_typeof(j) = 'array' THEN
        cleared := '[]'::jsonb;
        FOR e IN SELECT value FROM jsonb_array_elements(j) LOOP
          CONTINUE WHEN pg_temp.itgraph_is_user(e)
            OR coalesce(e->>'_' IN ('MessageEntityMentionName',
                                    'InputMessageEntityMentionName'), false)
            OR pg_temp.itgraph_is_user(e->'peer_id');
          cleared := cleared
            || jsonb_build_array(pg_temp.itgraph_without_people(e));
        END LOOP;
        RETURN cleared;
      END IF;
      RETURN j;
    END
    $f$
    """,
)


def upgrade() -> None:
    """Remove what names a person from every stored payload, then rewrite."""
    for statement in FUNCTIONS:
        op.execute(statement)
    for table in TABLES:
        op.execute(
            f"UPDATE {table} SET payload = pg_temp.itgraph_without_people(payload) "
            f"WHERE jsonb_path_exists(payload, '{NAMES_SOMEONE}')"
        )
    # VACUUM cannot run in a transaction: this commits what came before.
    with op.get_context().autocommit_block():
        for table in TABLES:
            op.execute(f"VACUUM (FULL, ANALYZE) {table}")


def downgrade() -> None:
    """Nothing: what the upgrade removed cannot come back."""
