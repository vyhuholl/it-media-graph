"""families without a canonical channel

Revision ID: 9231985c78b0
Revises: 214bc7f2b255
Create Date: 2026-07-30 17:23:34.248676

A family is a set, not a star. `channels.operator_id` named a canonical
channel that every member pointed at, and that shape could not hold what
detection actually finds: the pairs among one author's channels form an
arbitrary graph, so confirming them in the order they were ranked was
refused half the time, and which family came out depended on that order.

The column was also entirely redundant. Every `operator_id` was written
alongside a confirmed row in `affiliation_candidates`, so the pairs
already hold the whole fact. This drops a derived column that was never
the source of truth and reads the source directly, through the
`channel_families` view.

**The upgrade refuses to run if that redundancy does not hold.** It was
measured — 17 pairs, 17 pointers, 0 orphans — but nothing in the schema
guarantees it, and a migration that quietly discards a fact it assumed
was duplicated is exactly what the backup rule exists for.

The downgrade is honestly lossy: it recreates the columns and picks an
arbitrary channel per family, because *which* channel was canonical is
precisely the fact the upgrade destroys. Every family and every pair
survives both directions; only the designation does not.

"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision: str = "9231985c78b0"
down_revision: str | Sequence[str] | None = "214bc7f2b255"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

# The connected components of the confirmed pairs: one row per channel
# that shares an author with at least one other, and the key of the set
# it belongs to. A channel absent from here is its own family of one, so
# the family of any channel is `COALESCE(family_key, tg_id)` — the shape
# the dropped column had, with a left join in place of a column read.
#
# `UNION`, never `UNION ALL`. The pairs among one author's channels
# contain cycles by construction — A-B, B-C and A-C together are exactly
# the case that broke the star model — and `UNION ALL` would not
# terminate on one.
#
# `family_key` is the smallest id in the set. That is a deterministic
# *label* for the component and nothing more: no code treats that channel
# differently and it is never displayed. It is emphatically not a
# canonical channel returning under another name.
CHANNEL_FAMILIES_VIEW = """
CREATE VIEW channel_families AS
WITH RECURSIVE linked AS (
    SELECT channel_a AS channel_id, channel_b AS reached
      FROM affiliation_candidates WHERE decision = 'confirmed'
    UNION
    SELECT channel_b AS channel_id, channel_a AS reached
      FROM affiliation_candidates WHERE decision = 'confirmed'
),
reach AS (
    SELECT channel_id, reached FROM linked
    UNION
    SELECT r.channel_id, l.reached
      FROM reach r JOIN linked l ON l.channel_id = r.reached
)
SELECT channel_id, LEAST(MIN(reached), channel_id) AS family_key
  FROM reach
 GROUP BY channel_id
"""

# The same recursion, for the downgrade — by then the view is gone.
FAMILIES_CTE = """
WITH RECURSIVE linked AS (
    SELECT channel_a AS channel_id, channel_b AS reached
      FROM affiliation_candidates WHERE decision = 'confirmed'
    UNION
    SELECT channel_b AS channel_id, channel_a AS reached
      FROM affiliation_candidates WHERE decision = 'confirmed'
),
reach AS (
    SELECT channel_id, reached FROM linked
    UNION
    SELECT r.channel_id, l.reached
      FROM reach r JOIN linked l ON l.channel_id = r.reached
),
families AS (
    SELECT channel_id, LEAST(MIN(reached), channel_id) AS family_key
      FROM reach GROUP BY channel_id
)
"""

# Every channel carrying an `operator_id` must be *reachable* from it
# through the confirmed pairs — not necessarily paired with it directly.
# The distinction is load-bearing: the retired `recanonicalize_family`
# rewrote pointers without creating pairs, so a family confirmed as A-B
# and A-C and then re-headed on B leaves C pointing at B with no C-B pair
# and the family perfectly intact. Demanding a direct pair would refuse
# to migrate a database that has nothing wrong with it.
#
# Counted before anything is dropped; a non-zero answer stops the
# migration, because those are the pointers the pairs could not rebuild.
ORPHAN_POINTERS = (
    FAMILIES_CTE
    + """
SELECT count(*) FROM channels c
 LEFT JOIN families f ON f.channel_id = c.tg_id
 LEFT JOIN families o ON o.channel_id = c.operator_id
 WHERE c.operator_id IS NOT NULL
   AND (f.family_key IS DISTINCT FROM o.family_key
        OR f.family_key IS NULL)
"""
)


def upgrade() -> None:
    """Upgrade schema."""
    orphans = op.get_bind().execute(sa.text(ORPHAN_POINTERS)).scalar_one()
    if orphans:
        raise RuntimeError(
            f"{orphans} channels carry an operator_id that the confirmed "
            "pairs cannot reach; dropping the column would lose those "
            "families, because the pairs are what this migration keeps. "
            "Inspect them with: SELECT tg_id, operator_id FROM channels "
            "WHERE operator_id IS NOT NULL; and record the missing pairs "
            "with `itgraph family` before upgrading."
        )

    op.execute(CHANNEL_FAMILIES_VIEW)

    op.drop_constraint(
        op.f("ck_channels_operator_is_another_channel"),
        "channels",
        type_="check",
    )
    op.drop_constraint(
        op.f("fk_channels_operator_id_channels"),
        "channels",
        type_="foreignkey",
    )
    op.drop_column("channels", "operator_id")

    op.drop_constraint(
        op.f("ck_affiliation_candidates_canonical_only_when_confirmed"),
        "affiliation_candidates",
        type_="check",
    )
    op.drop_constraint(
        op.f("fk_affiliation_candidates_canonical_id_channels"),
        "affiliation_candidates",
        type_="foreignkey",
    )
    op.drop_column("affiliation_candidates", "canonical_id")


def downgrade() -> None:
    """Downgrade schema.

    Lossy by construction — see the module docstring. The columns come
    back and are repopulated from the confirmed pairs, with the smallest
    channel of each family taken as canonical.
    """
    op.execute("DROP VIEW IF EXISTS channel_families")

    op.add_column(
        "affiliation_candidates",
        sa.Column("canonical_id", sa.BigInteger(), nullable=True),
    )
    op.create_foreign_key(
        op.f("fk_affiliation_candidates_canonical_id_channels"),
        "affiliation_candidates",
        "channels",
        ["canonical_id"],
        ["tg_id"],
        ondelete="SET NULL",
    )
    op.add_column(
        "channels", sa.Column("operator_id", sa.BigInteger(), nullable=True)
    )
    op.create_foreign_key(
        op.f("fk_channels_operator_id_channels"),
        "channels",
        "channels",
        ["operator_id"],
        ["tg_id"],
        ondelete="SET NULL",
    )

    # Rebuild the star: every member points at its family's smallest
    # channel, which points at nobody.
    op.execute(
        FAMILIES_CTE
        + """
        UPDATE channels c
           SET operator_id = f.family_key
          FROM families f
         WHERE f.channel_id = c.tg_id
           AND f.family_key <> c.tg_id
        """
    )
    # The restored check demands a canonical channel on every confirmed
    # row, so each one names its family's.
    op.execute(
        FAMILIES_CTE
        + """
        UPDATE affiliation_candidates a
           SET canonical_id = f.family_key
          FROM families f
         WHERE f.channel_id = a.channel_a
           AND a.decision = 'confirmed'
        """
    )

    op.create_check_constraint(
        "canonical_only_when_confirmed",
        "affiliation_candidates",
        "(canonical_id IS NOT NULL) = (decision = 'confirmed')",
    )
    op.create_check_constraint(
        "operator_is_another_channel",
        "channels",
        "operator_id IS NULL OR operator_id <> tg_id",
    )
