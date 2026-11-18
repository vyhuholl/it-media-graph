"""youtube affiliation

Revision ID: d4a7f2e83c16
Revises: c060371f685d
Create Date: 2026-11-18 20:00:00.000000

Which YouTube channels share an owner — an author or an organisation —
so the same semi-automatic review Telegram has can run there:
`itgraph yt affiliates` proposes, `itgraph yt family` decides.

Two tables and a view, `tg_affiliation_runs`/`tg_affiliation_candidates`
/`tg_channel_families` mirrored onto YouTube ids. Three differences from
the Telegram shape, all measured rather than assumed (the corpus: 887
seeds, 1127 payloads, 795 descriptions):

* The pair columns are Text, not BigInteger — YouTube channel ids are
  strings — so the pair's canonical order is lexicographic rather than
  numeric, and the CHECK says `channel_a < channel_b` over text.
* The evidence columns are the three signals the corpus supports: a
  description reference's direction, a rarity-capped shared title token,
  and a token corroborated by a reference between its carriers. No
  concentration or mutuality columns — no YouTube seed reaches even ten
  distinct reference targets, and 24 pairs have a reference each way at
  all.
* The run records `with_payload` rather than Telegram's coverage of
  descriptions alone: a YouTube channel's title exists only while its
  payload does, so the payloads are the denominator of everything.

Nothing is backfilled. The candidates come from signals computed over
the stored payloads, which are already here; one `itgraph yt affiliates`
run fills the table, spends no quota, and can be re-run under different
thresholds as often as the operator likes.

Three enum types are created rather than shared with the Telegram
tables (`yt_ref_direction`, `yt_affiliation_decision`,
`yt_candidate_origin`): the values are the same, but a shared type
would couple the two platforms' schemas, and dropping one platform's
tables must never have to consider the other's.

"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision: str = "d4a7f2e83c16"
down_revision: str | Sequence[str] | None = "c060371f685d"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

# `drop_table` leaves enum types behind, and the next `upgrade` would
# then fail on a type that already exists. Named so the downgrade can
# drop them explicitly.
ENUM_TYPES = (
    "yt_ref_direction",
    "yt_affiliation_decision",
    "yt_candidate_origin",
)

# The connected components of the confirmed pairs — the same recursion
# `tg_channel_families` runs, over the YouTube pairs. `UNION`, never
# `UNION ALL`: the pairs among one owner's channels contain cycles by
# construction. `family_key` is the lexicographically smallest id in the
# set: a deterministic label, not a canonical channel.
YT_CHANNEL_FAMILIES_VIEW = """
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


def upgrade() -> None:
    """Upgrade schema."""
    op.create_table(
        "yt_affiliation_runs",
        sa.Column("id", sa.BigInteger(), nullable=False),
        sa.Column(
            "ran_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column("min_token_length", sa.Integer(), nullable=False),
        sa.Column("max_token_channels", sa.Integer(), nullable=False),
        sa.Column("max_corroborated_channels", sa.Integer(), nullable=False),
        sa.Column("weight_reference", sa.Float(), nullable=False),
        sa.Column("weight_group", sa.Float(), nullable=False),
        sa.Column("weight_token", sa.Float(), nullable=False),
        sa.Column("channels_scored", sa.Integer(), nullable=False),
        sa.Column("with_payload", sa.Integer(), nullable=False),
        sa.Column("with_description", sa.Integer(), nullable=False),
        sa.Column("refs_outside_inventory", sa.Integer(), nullable=False),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_yt_affiliation_runs")),
    )
    op.create_table(
        "yt_affiliation_candidates",
        sa.Column("channel_a", sa.Text(), nullable=False),
        sa.Column("channel_b", sa.Text(), nullable=False),
        sa.Column("score", sa.Float(), nullable=False),
        sa.Column("run_id", sa.BigInteger(), nullable=True),
        sa.Column(
            "ref_direction",
            sa.Enum("a_to_b", "b_to_a", "mutual", name="yt_ref_direction"),
            nullable=True,
        ),
        sa.Column("shared_token", sa.Text(), nullable=True),
        sa.Column("shared_token_channels", sa.Integer(), nullable=True),
        sa.Column("corroborated_token", sa.Text(), nullable=True),
        sa.Column("corroborated_token_channels", sa.Integer(), nullable=True),
        sa.Column(
            "decision",
            sa.Enum(
                "pending",
                "confirmed",
                "rejected",
                name="yt_affiliation_decision",
            ),
            server_default="pending",
            nullable=False,
        ),
        sa.Column(
            "origin",
            sa.Enum("signal", "operator", name="yt_candidate_origin"),
            server_default="signal",
            nullable=False,
        ),
        sa.Column("decided_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("decision_note", sa.Text(), nullable=True),
        sa.Column(
            "detected_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.CheckConstraint(
            "(decision = 'pending') = (decided_at IS NULL)",
            name=op.f("ck_yt_affiliation_candidates_decided_has_timestamp"),
        ),
        sa.CheckConstraint(
            "channel_a < channel_b",
            name=op.f("ck_yt_affiliation_candidates_pair_is_ordered"),
        ),
        sa.ForeignKeyConstraint(
            ["channel_a"],
            ["yt_channels.channel_id"],
            name=op.f("fk_yt_affiliation_candidates_channel_a_yt_channels"),
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["channel_b"],
            ["yt_channels.channel_id"],
            name=op.f("fk_yt_affiliation_candidates_channel_b_yt_channels"),
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["run_id"],
            ["yt_affiliation_runs.id"],
            name=op.f(
                "fk_yt_affiliation_candidates_run_id_yt_affiliation_runs"
            ),
            ondelete="SET NULL",
        ),
        sa.PrimaryKeyConstraint(
            "channel_a", "channel_b", name=op.f("pk_yt_affiliation_candidates")
        ),
    )
    op.create_index(
        "ix_yt_affiliation_candidates_score",
        "yt_affiliation_candidates",
        ["score"],
        unique=False,
    )
    op.execute(YT_CHANNEL_FAMILIES_VIEW)


def downgrade() -> None:
    """Downgrade schema."""
    op.execute("DROP VIEW IF EXISTS yt_channel_families")
    op.drop_index(
        "ix_yt_affiliation_candidates_score",
        table_name="yt_affiliation_candidates",
    )
    op.drop_table("yt_affiliation_candidates")
    op.drop_table("yt_affiliation_runs")
    for name in ENUM_TYPES:
        op.execute(f"DROP TYPE IF EXISTS {name}")
