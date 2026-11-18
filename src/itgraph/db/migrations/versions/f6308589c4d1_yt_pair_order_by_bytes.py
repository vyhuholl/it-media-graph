"""yt pair order by bytes

Revision ID: f6308589c4d1
Revises: d4a7f2e83c16
Create Date: 2026-11-18 16:04:39.626248

The pair-order CHECK compares ``Text`` under the database's default
collation, which on the collection host is ``en_US.utf8`` — a
linguistic order, case-insensitive at its primary level: it ranks
``UCa…`` before ``UCB…`` where code points rank ``UCB…`` first.
Detection orders every pair in Python, by code points (``ordered()``
in ``yt/affiliation/signals.py``), and the two orders disagree for
about a fifth of all pairs of real ids — measured on the live
inventory, 135 704 of ~635 000. One bulk insert of hundreds of pairs
therefore reliably carries a disagreement, and the first
``itgraph yt affiliates`` run in production died whole on its
candidates insert, every run rolling back with it. The confirm and
reject writes hit the same CHECK and would have died the same way.

The order is now said out loud: the CHECK, and the ``LEAST``/``MIN``
that pick a family key in ``yt_channel_families``, compare under
``COLLATE "C"`` — UTF-8 byte order, which is code-point order, the
one Python used. The view is recreated rather than altered because a
view keeps the collation it was created with.

The columns themselves keep the database collation. These are the two
comparisons whose result depends on the choice, and pinning the whole
column would make every future ``ORDER BY`` over ids byte order too —
a decision nobody asked this migration to make.

"""

from collections.abc import Sequence

from alembic import op

# revision identifiers, used by Alembic.
revision: str = "f6308589c4d1"
down_revision: str | Sequence[str] | None = "d4a7f2e83c16"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

# The constraint's full name, as `d4a7f2e83c16` created it inside
# `create_table`. Naming applies to the short form on the way in — the
# metadata's convention prefixes it — while a drop names the final one
# through `op.f`, which says the name is already complete.
CONSTRAINT = "ck_yt_affiliation_candidates_pair_is_ordered"

# What `d4a7f2e83c16` created; the downgrade restores exactly that.
PREVIOUS_VIEW = """
CREATE VIEW yt_channel_families AS
WITH RECURSIVE linked AS (
    SELECT channel_a AS channel_id, channel_b AS reached
      FROM yt_affiliation_candidates WHERE decision = 'confirmed'
    UNION
    SELECT channel_b AS channel_id, channel_a AS reached
      FROM yt_affiliation_candidates WHERE decision = 'confirmed'
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

# The same view with the smallest-id comparisons collated to "C". The
# living copy of this SQL is `db/views.py`; this one exists because a
# migration must say what the database looked like at its revision,
# not what the module says today.
VIEW = """
CREATE VIEW yt_channel_families AS
WITH RECURSIVE linked AS (
    SELECT channel_a AS channel_id, channel_b AS reached
      FROM yt_affiliation_candidates WHERE decision = 'confirmed'
    UNION
    SELECT channel_b AS channel_id, channel_a AS reached
      FROM yt_affiliation_candidates WHERE decision = 'confirmed'
),
reach AS (
    SELECT channel_id, reached FROM linked
    UNION
    SELECT r.channel_id, l.reached
      FROM reach r JOIN linked l ON l.channel_id = r.reached
)
SELECT channel_id,
       LEAST(MIN(reached COLLATE "C"), channel_id COLLATE "C") AS family_key
  FROM reach
 GROUP BY channel_id
"""


def upgrade() -> None:
    """Upgrade schema."""
    op.drop_constraint(
        op.f(CONSTRAINT), "yt_affiliation_candidates", type_="check"
    )
    op.create_check_constraint(
        "pair_is_ordered",
        "yt_affiliation_candidates",
        '(channel_a COLLATE "C") < (channel_b COLLATE "C")',
    )
    op.execute("DROP VIEW yt_channel_families")
    op.execute(VIEW)


def downgrade() -> None:
    """Downgrade schema."""
    op.execute("DROP VIEW yt_channel_families")
    op.execute(PREVIOUS_VIEW)
    op.drop_constraint(
        op.f(CONSTRAINT), "yt_affiliation_candidates", type_="check"
    )
    op.create_check_constraint(
        "pair_is_ordered",
        "yt_affiliation_candidates",
        "channel_a < channel_b",
    )
