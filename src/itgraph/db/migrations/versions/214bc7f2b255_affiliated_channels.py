"""affiliated channels

Revision ID: 214bc7f2b255
Revises: 8bf31de7de06
Create Date: 2026-07-30 11:43:43.672389

Which channels share an author, so the role metrics can stop counting one
person's several channels as several people.

Three things, all additive. `channels.operator_id` names the canonical
channel of a family — shaped like `linked_to`, so members point at it and
it points at nobody, and the family of any channel is
`COALESCE(operator_id, tg_id)`. `affiliation_runs` holds one row per
detection run with the thresholds it used. `affiliation_candidates` holds
the proposed pairs and the evidence behind each.

Nothing is backfilled. The candidates come from signals computed over the
inventory, the edges and the stored channel payloads, all of which are
already here; one `itgraph affiliates` run fills the table, spends no
network request, and can be re-run under different thresholds as often as
the operator likes.

`operator_id` is deliberately left for that review to write. No
threshold on this data separates an author's second channel from a close
collaborator, so a migration guessing at families would be writing
exactly the fact the whole change exists to establish by hand.

The depth-one rule — `operator_id` must name a channel whose own
`operator_id` is null — is not a constraint here. A CHECK cannot see
another row, and a trigger would be this project's first, on its
most-written table. `db/channels.py` enforces it at the single write
path.

"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision: str = "214bc7f2b255"
down_revision: str | Sequence[str] | None = "8bf31de7de06"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

# `drop_table` leaves enum types behind, and the next `upgrade` would
# then fail on a type that already exists. Named so the downgrade can
# drop them explicitly.
ENUM_TYPES = ("about_direction", "affiliation_decision", "candidate_origin")


def upgrade() -> None:
    """Upgrade schema."""
    op.create_table(
        "affiliation_runs",
        sa.Column("id", sa.BigInteger(), nullable=False),
        sa.Column(
            "ran_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column("min_out_edges", sa.Integer(), nullable=False),
        sa.Column("max_share_min", sa.Float(), nullable=False),
        sa.Column("min_token_length", sa.Integer(), nullable=False),
        sa.Column("max_token_channels", sa.Integer(), nullable=False),
        sa.Column("min_mutual_edges", sa.Integer(), nullable=False),
        sa.Column("edge_kinds", sa.ARRAY(sa.Text()), nullable=False),
        sa.Column("weight_about", sa.Float(), nullable=False),
        sa.Column("weight_token", sa.Float(), nullable=False),
        sa.Column("weight_share", sa.Float(), nullable=False),
        sa.Column("weight_mutual", sa.Float(), nullable=False),
        sa.Column("channels_scored", sa.Integer(), nullable=False),
        sa.Column("with_description", sa.Integer(), nullable=False),
        sa.Column("refs_outside_inventory", sa.Integer(), nullable=False),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_affiliation_runs")),
    )
    op.create_table(
        "affiliation_candidates",
        sa.Column(
            "channel_a", sa.BigInteger(), autoincrement=False, nullable=False
        ),
        sa.Column(
            "channel_b", sa.BigInteger(), autoincrement=False, nullable=False
        ),
        sa.Column("score", sa.Float(), nullable=False),
        sa.Column("run_id", sa.BigInteger(), nullable=True),
        sa.Column(
            "about_direction",
            sa.Enum("a_to_b", "b_to_a", "mutual", name="about_direction"),
            nullable=True,
        ),
        sa.Column("shared_token", sa.Text(), nullable=True),
        sa.Column("shared_token_channels", sa.Integer(), nullable=True),
        sa.Column("out_share", sa.Float(), nullable=True),
        sa.Column("out_share_edges", sa.Integer(), nullable=True),
        sa.Column("out_share_src", sa.BigInteger(), nullable=True),
        sa.Column("edges_a_to_b", sa.Integer(), nullable=True),
        sa.Column("edges_b_to_a", sa.Integer(), nullable=True),
        sa.Column(
            "decision",
            sa.Enum(
                "pending",
                "confirmed",
                "rejected",
                name="affiliation_decision",
            ),
            server_default="pending",
            nullable=False,
        ),
        sa.Column(
            "origin",
            sa.Enum("signal", "operator", name="candidate_origin"),
            server_default="signal",
            nullable=False,
        ),
        sa.Column("canonical_id", sa.BigInteger(), nullable=True),
        sa.Column("decided_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("decision_note", sa.Text(), nullable=True),
        sa.Column(
            "detected_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.CheckConstraint(
            "(canonical_id IS NOT NULL) = (decision = 'confirmed')",
            name=op.f(
                "ck_affiliation_candidates_canonical_only_when_confirmed"
            ),
        ),
        sa.CheckConstraint(
            "(decision = 'pending') = (decided_at IS NULL)",
            name=op.f("ck_affiliation_candidates_decided_has_timestamp"),
        ),
        sa.CheckConstraint(
            "channel_a < channel_b",
            name=op.f("ck_affiliation_candidates_pair_is_ordered"),
        ),
        sa.ForeignKeyConstraint(
            ["canonical_id"],
            ["channels.tg_id"],
            name=op.f("fk_affiliation_candidates_canonical_id_channels"),
            ondelete="SET NULL",
        ),
        sa.ForeignKeyConstraint(
            ["channel_a"],
            ["channels.tg_id"],
            name=op.f("fk_affiliation_candidates_channel_a_channels"),
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["channel_b"],
            ["channels.tg_id"],
            name=op.f("fk_affiliation_candidates_channel_b_channels"),
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["run_id"],
            ["affiliation_runs.id"],
            name=op.f("fk_affiliation_candidates_run_id_affiliation_runs"),
            ondelete="SET NULL",
        ),
        sa.PrimaryKeyConstraint(
            "channel_a", "channel_b", name=op.f("pk_affiliation_candidates")
        ),
    )
    op.create_index(
        "ix_affiliation_candidates_score",
        "affiliation_candidates",
        ["score"],
        unique=False,
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
    # Autogenerate does not compare check constraints, so this one is
    # written by hand or not at all. It is the cheap half of the family
    # invariant: a channel naming itself is refused by the database, and
    # a chain by the one function that writes the column.
    op.create_check_constraint(
        "operator_is_another_channel",
        "channels",
        "operator_id IS NULL OR operator_id <> tg_id",
    )


def downgrade() -> None:
    """Downgrade schema."""
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
    op.drop_index(
        "ix_affiliation_candidates_score", table_name="affiliation_candidates"
    )
    op.drop_table("affiliation_candidates")
    op.drop_table("affiliation_runs")
    for name in ENUM_TYPES:
        op.execute(f"DROP TYPE IF EXISTS {name}")
