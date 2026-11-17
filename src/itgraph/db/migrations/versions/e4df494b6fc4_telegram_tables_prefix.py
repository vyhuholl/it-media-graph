"""telegram tables prefix

Revision ID: e4df494b6fc4
Revises: b092acf2a3ff
Create Date: 2026-11-16 23:28:00.730420

Every Telegram table and the view take `tg_`, as YouTube's carry `yt_`,
and so do the enum types only Telegram uses. What both platforms share
keeps no prefix: `channel_status`, `baseline_reference`, `alembic_version`.

**Metadata only.** No row is rewritten. Grants, default privileges,
column defaults and the view's stored definition follow each object by its
identity, not its name.

**What is named after a table follows it.** Postgres renames neither the
constraints and indexes the naming convention built from a table's name,
nor a `serial` column's sequence. Each is renamed here to what the models
now give, which `tests/test_schema_names.py` holds them to.

**A literal list, not the catalog.** A revision is frozen code: one that
renamed whatever matched when it ran would one day rename something added
after it. Downgrade walks the same lists the other way.
"""

from collections.abc import Sequence

from alembic import op

# revision identifiers, used by Alembic.
revision: str = "e4df494b6fc4"
down_revision: str | Sequence[str] | None = "b092acf2a3ff"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

TABLES: list[tuple[str, str]] = [
    ("affiliation_candidates", "tg_affiliation_candidates"),
    ("affiliation_runs", "tg_affiliation_runs"),
    ("alerts", "tg_alerts"),
    ("backfill_state", "tg_backfill_state"),
    ("baseline_runs", "tg_baseline_runs"),
    ("channel_baselines", "tg_channel_baselines"),
    ("channels", "tg_channels"),
    ("curve_points", "tg_curve_points"),
    ("edges", "tg_edges"),
    ("flood_events", "tg_flood_events"),
    ("message_metrics", "tg_message_metrics"),
    ("metric_baselines", "tg_metric_baselines"),
    ("pending_mention_sources", "tg_pending_mention_sources"),
    ("pending_mentions", "tg_pending_mentions"),
    ("poll_state", "tg_poll_state"),
    ("raw_channels", "tg_raw_channels"),
    ("raw_messages", "tg_raw_messages"),
    ("weekly_entries", "tg_weekly_entries"),
    ("weekly_lists", "tg_weekly_lists"),
    ("weekly_revivals", "tg_weekly_revivals"),
]

VIEWS: list[tuple[str, str]] = [
    ("channel_families", "tg_channel_families"),
]

TYPES: list[tuple[str, str]] = [
    ("about_direction", "tg_about_direction"),
    ("affiliation_decision", "tg_affiliation_decision"),
    ("alert_delivery", "tg_alert_delivery"),
    ("alert_kind", "tg_alert_kind"),
    ("backfill_status", "tg_backfill_status"),
    ("candidate_origin", "tg_candidate_origin"),
    ("channel_kind", "tg_channel_kind"),
    ("collection_command", "tg_collection_command"),
    ("discovery_source", "tg_discovery_source"),
    ("edge_kind", "tg_edge_kind"),
    ("failure_kind", "tg_failure_kind"),
    ("metric", "tg_metric"),
    ("reject_reason", "tg_reject_reason"),
]

# (table, old, new): the table by its new name, which it has by then.
CONSTRAINTS: list[tuple[str, str, str]] = [
    (
        "tg_affiliation_candidates",
        "ck_affiliation_candidates_decided_has_timestamp",
        "ck_tg_affiliation_candidates_decided_has_timestamp",
    ),
    (
        "tg_affiliation_candidates",
        "ck_affiliation_candidates_pair_is_ordered",
        "ck_tg_affiliation_candidates_pair_is_ordered",
    ),
    (
        "tg_affiliation_candidates",
        "fk_affiliation_candidates_channel_a_channels",
        "fk_tg_affiliation_candidates_channel_a_tg_channels",
    ),
    (
        "tg_affiliation_candidates",
        "fk_affiliation_candidates_channel_b_channels",
        "fk_tg_affiliation_candidates_channel_b_tg_channels",
    ),
    (
        "tg_affiliation_candidates",
        "fk_affiliation_candidates_run_id_affiliation_runs",
        "fk_tg_affiliation_candidates_run_id_tg_affiliation_runs",
    ),
    (
        "tg_affiliation_candidates",
        "pk_affiliation_candidates",
        "pk_tg_affiliation_candidates",
    ),
    ("tg_affiliation_runs", "pk_affiliation_runs", "pk_tg_affiliation_runs"),
    (
        "tg_alerts",
        "ck_alerts_not_folded_into_itself",
        "ck_tg_alerts_not_folded_into_itself",
    ),
    (
        "tg_alerts",
        "fk_alerts_channel_id_raw_messages",
        "fk_tg_alerts_channel_id_tg_raw_messages",
    ),
    (
        "tg_alerts",
        "fk_alerts_folded_into_alerts",
        "fk_tg_alerts_folded_into_tg_alerts",
    ),
    ("tg_alerts", "pk_alerts", "pk_tg_alerts"),
    ("tg_alerts", "uq_alerts_post_band", "uq_tg_alerts_post_band"),
    (
        "tg_backfill_state",
        "fk_backfill_state_channel_id_channels",
        "fk_tg_backfill_state_channel_id_tg_channels",
    ),
    ("tg_backfill_state", "pk_backfill_state", "pk_tg_backfill_state"),
    ("tg_baseline_runs", "pk_baseline_runs", "pk_tg_baseline_runs"),
    (
        "tg_channel_baselines",
        "fk_channel_baselines_channel_id_channels",
        "fk_tg_channel_baselines_channel_id_tg_channels",
    ),
    (
        "tg_channel_baselines",
        "fk_channel_baselines_run_id_baseline_runs",
        "fk_tg_channel_baselines_run_id_tg_baseline_runs",
    ),
    (
        "tg_channel_baselines",
        "pk_channel_baselines",
        "pk_tg_channel_baselines",
    ),
    (
        "tg_channels",
        "ck_channels_rejected_has_reason",
        "ck_tg_channels_rejected_has_reason",
    ),
    (
        "tg_channels",
        "fk_channels_linked_to_channels",
        "fk_tg_channels_linked_to_tg_channels",
    ),
    ("tg_channels", "pk_channels", "pk_tg_channels"),
    (
        "tg_curve_points",
        "fk_curve_points_run_id_baseline_runs",
        "fk_tg_curve_points_run_id_tg_baseline_runs",
    ),
    ("tg_curve_points", "pk_curve_points", "pk_tg_curve_points"),
    (
        "tg_edges",
        "fk_edges_dst_channel_id_channels",
        "fk_tg_edges_dst_channel_id_tg_channels",
    ),
    (
        "tg_edges",
        "fk_edges_src_channel_id_channels",
        "fk_tg_edges_src_channel_id_tg_channels",
    ),
    ("tg_edges", "pk_edges", "pk_tg_edges"),
    ("tg_edges", "uq_edges_reference", "uq_tg_edges_reference"),
    (
        "tg_flood_events",
        "fk_flood_events_channel_id_channels",
        "fk_tg_flood_events_channel_id_tg_channels",
    ),
    ("tg_flood_events", "pk_flood_events", "pk_tg_flood_events"),
    (
        "tg_message_metrics",
        "fk_message_metrics_channel_id_raw_messages",
        "fk_tg_message_metrics_channel_id_tg_raw_messages",
    ),
    ("tg_message_metrics", "pk_message_metrics", "pk_tg_message_metrics"),
    (
        "tg_metric_baselines",
        "fk_metric_baselines_run_id_baseline_runs",
        "fk_tg_metric_baselines_run_id_tg_baseline_runs",
    ),
    ("tg_metric_baselines", "pk_metric_baselines", "pk_tg_metric_baselines"),
    (
        "tg_pending_mention_sources",
        "fk_pending_mention_sources_username_pending_mentions",
        "fk_tg_pending_mention_sources_username_tg_pending_mentions",
    ),
    (
        "tg_pending_mention_sources",
        "pk_pending_mention_sources",
        "pk_tg_pending_mention_sources",
    ),
    ("tg_pending_mentions", "pk_pending_mentions", "pk_tg_pending_mentions"),
    (
        "tg_poll_state",
        "fk_poll_state_channel_id_channels",
        "fk_tg_poll_state_channel_id_tg_channels",
    ),
    ("tg_poll_state", "pk_poll_state", "pk_tg_poll_state"),
    (
        "tg_raw_channels",
        "fk_raw_channels_channel_id_channels",
        "fk_tg_raw_channels_channel_id_tg_channels",
    ),
    ("tg_raw_channels", "pk_raw_channels", "pk_tg_raw_channels"),
    (
        "tg_raw_messages",
        "fk_raw_messages_channel_id_channels",
        "fk_tg_raw_messages_channel_id_tg_channels",
    ),
    ("tg_raw_messages", "pk_raw_messages", "pk_tg_raw_messages"),
    (
        "tg_weekly_entries",
        "fk_weekly_entries_channel_id_channels",
        "fk_tg_weekly_entries_channel_id_tg_channels",
    ),
    (
        "tg_weekly_entries",
        "fk_weekly_entries_channel_id_message_metrics",
        "fk_tg_weekly_entries_channel_id_tg_message_metrics",
    ),
    (
        "tg_weekly_entries",
        "fk_weekly_entries_list_id_weekly_lists",
        "fk_tg_weekly_entries_list_id_tg_weekly_lists",
    ),
    ("tg_weekly_entries", "pk_weekly_entries", "pk_tg_weekly_entries"),
    (
        "tg_weekly_entries",
        "uq_weekly_entries_channel",
        "uq_tg_weekly_entries_channel",
    ),
    ("tg_weekly_lists", "pk_weekly_lists", "pk_tg_weekly_lists"),
    ("tg_weekly_lists", "uq_weekly_lists_week", "uq_tg_weekly_lists_week"),
    (
        "tg_weekly_revivals",
        "fk_weekly_revivals_channel_id_channels",
        "fk_tg_weekly_revivals_channel_id_tg_channels",
    ),
    (
        "tg_weekly_revivals",
        "fk_weekly_revivals_channel_id_message_metrics",
        "fk_tg_weekly_revivals_channel_id_tg_message_metrics",
    ),
    (
        "tg_weekly_revivals",
        "fk_weekly_revivals_list_id_weekly_lists",
        "fk_tg_weekly_revivals_list_id_tg_weekly_lists",
    ),
    ("tg_weekly_revivals", "pk_weekly_revivals", "pk_tg_weekly_revivals"),
    (
        "tg_weekly_revivals",
        "uq_weekly_revivals_channel",
        "uq_tg_weekly_revivals_channel",
    ),
]
INDEXES: list[tuple[str, str]] = [
    ("ix_affiliation_candidates_score", "ix_tg_affiliation_candidates_score"),
    ("ix_alerts_folded_into", "ix_tg_alerts_folded_into"),
    ("ix_alerts_undelivered", "ix_tg_alerts_undelivered"),
    (
        "ix_edges_dst_channel_id_dst_msg_id",
        "ix_tg_edges_dst_channel_id_dst_msg_id",
    ),
    ("ix_edges_dst_channel_id", "ix_tg_edges_dst_channel_id"),
    ("ix_edges_published_at", "ix_tg_edges_published_at"),
    ("ix_edges_src_channel_id", "ix_tg_edges_src_channel_id"),
    ("ix_flood_events_method", "ix_tg_flood_events_method"),
    ("ix_flood_events_occurred_at", "ix_tg_flood_events_occurred_at"),
    ("ix_message_metrics_observed_at", "ix_tg_message_metrics_observed_at"),
    ("ix_poll_state_due_at", "ix_tg_poll_state_due_at"),
    ("ix_weekly_lists_undelivered", "ix_tg_weekly_lists_undelivered"),
]
SEQUENCES: list[tuple[str, str]] = [
    ("affiliation_runs_id_seq", "tg_affiliation_runs_id_seq"),
    ("alerts_id_seq", "tg_alerts_id_seq"),
    ("baseline_runs_id_seq", "tg_baseline_runs_id_seq"),
    ("edges_id_seq", "tg_edges_id_seq"),
    ("flood_events_id_seq", "tg_flood_events_id_seq"),
    ("weekly_lists_id_seq", "tg_weekly_lists_id_seq"),
]


def _rename(old_to_new: bool) -> None:
    def pick(old: str, new: str) -> tuple[str, str]:
        return (old, new) if old_to_new else (new, old)

    for old, new in TABLES:
        source, target = pick(old, new)
        op.execute(f"ALTER TABLE {source} RENAME TO {target}")
    for old, new in VIEWS:
        source, target = pick(old, new)
        op.execute(f"ALTER VIEW {source} RENAME TO {target}")
    for old, new in TYPES:
        source, target = pick(old, new)
        op.execute(f"ALTER TYPE {source} RENAME TO {target}")
    for old, new in SEQUENCES:
        source, target = pick(old, new)
        op.execute(f"ALTER SEQUENCE {source} RENAME TO {target}")
    for old, new in INDEXES:
        source, target = pick(old, new)
        op.execute(f"ALTER INDEX {source} RENAME TO {target}")


def _rename_constraints(old_to_new: bool) -> None:
    for table, old, new in CONSTRAINTS:
        source, target = (old, new) if old_to_new else (new, old)
        op.execute(
            f"ALTER TABLE {table} RENAME CONSTRAINT {source} TO {target}"
        )


def upgrade() -> None:
    # Tables first, so each constraint is reached through its new table.
    _rename(old_to_new=True)
    _rename_constraints(old_to_new=True)


def downgrade() -> None:
    # Constraints first, while their tables still carry the new names.
    _rename_constraints(old_to_new=False)
    _rename(old_to_new=False)
