"""Reads and writes of the derived tables: ``edges`` and ``pending_mentions``.

These two tables are disposable — everything in them is recomputable from
the raw layer — which is what makes ``derive --rebuild`` able to truncate
them without a second thought. Nothing here touches ``channels`` beyond
creating rows a reference discovered, and nothing here reads the network.
"""

from collections.abc import Iterable, Sequence
from dataclasses import dataclass, field
from datetime import UTC, datetime

from sqlalchemy import delete, select, text, update
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.ext.asyncio import AsyncSession

from itgraph.db.models import (
    Channel,
    DiscoverySource,
    Edge,
    EdgeKind,
    PendingMention,
)

__all__ = [
    "ChannelIndex",
    "EdgeRow",
    "add_pending_mentions",
    "create_discovered_channels",
    "delete_pending_mention",
    "insert_edges",
    "load_channel_index",
    "pending_mentions_to_resolve",
    "record_pending_failure",
    "truncate_derived",
]


@dataclass(frozen=True, slots=True)
class EdgeRow:
    """One edge, ready to insert. A plain carrier, not a model instance."""

    src_channel_id: int
    dst_channel_id: int
    kind: EdgeKind
    msg_id: int
    published_at: datetime


@dataclass(slots=True)
class ChannelIndex:
    """Which channels exist, and which username maps to which id.

    Held in memory for the length of a derivation run so a reference need
    not cost a query per message. It grows as forwards discover new
    channels, and stays consistent with the database because every
    discovery goes through ``add`` and is also written in the same batch.
    """

    ids: set[int] = field(default_factory=set)
    username_to_id: dict[str, int] = field(default_factory=dict)

    def add(self, tg_id: int, username: str | None = None) -> None:
        self.ids.add(tg_id)
        if username:
            self.username_to_id[username.lower()] = tg_id


async def load_channel_index(session: AsyncSession) -> ChannelIndex:
    """Every channel id and username the inventory currently holds.

    A username shared by two rows — a rename the last import has not yet
    reconciled — resolves to whichever row is read last. That is a rare
    and self-correcting ambiguity, and picking one is better than the
    query-per-mention it would take to avoid.
    """
    index = ChannelIndex()
    rows = await session.execute(select(Channel.tg_id, Channel.username))
    for tg_id, username in rows:
        index.add(tg_id, username)
    return index


async def create_discovered_channels(
    session: AsyncSession,
    *,
    tg_ids: Iterable[int],
    discovered_via: DiscoverySource,
) -> int:
    """Create rows for referenced channels not yet in the inventory.

    ``ON CONFLICT DO NOTHING`` so a channel that already exists keeps its
    provenance, review and first-seen timestamp untouched — discovery may
    add a record but never alter one. Returns how many were genuinely new.
    """
    ids = list(dict.fromkeys(tg_ids))
    if not ids:
        return 0
    statement = (
        insert(Channel)
        .values(
            [
                {"tg_id": tg_id, "discovered_via": discovered_via}
                for tg_id in ids
            ]
        )
        .on_conflict_do_nothing(index_elements=[Channel.tg_id])
        .returning(Channel.tg_id)
    )
    return len((await session.execute(statement)).all())


async def insert_edges(session: AsyncSession, edges: Sequence[EdgeRow]) -> int:
    """Insert observed edges, skipping any already recorded.

    ``ON CONFLICT DO NOTHING`` on the natural key ``(src, msg_id, kind,
    dst)`` makes a re-run over unchanged raw data a no-op: the second pass
    writes nothing. Returns how many rows were new.
    """
    if not edges:
        return 0
    statement = (
        insert(Edge)
        .values(
            [
                {
                    "src_channel_id": edge.src_channel_id,
                    "dst_channel_id": edge.dst_channel_id,
                    "kind": edge.kind,
                    "msg_id": edge.msg_id,
                    "published_at": edge.published_at,
                }
                for edge in edges
            ]
        )
        .on_conflict_do_nothing(
            index_elements=[
                Edge.src_channel_id,
                Edge.msg_id,
                Edge.kind,
                Edge.dst_channel_id,
            ]
        )
        .returning(Edge.id)
    )
    return len((await session.execute(statement)).all())


async def add_pending_mentions(
    session: AsyncSession, usernames: Iterable[str]
) -> int:
    """Record usernames mentioned but not yet known as channels.

    ``ON CONFLICT DO NOTHING`` keeps the original ``first_seen_at`` and
    any resolution attempts already made against a username seen before.
    Returns how many were newly pending.
    """
    names = list(dict.fromkeys(usernames))
    if not names:
        return 0
    statement = (
        insert(PendingMention)
        .values([{"username": name} for name in names])
        .on_conflict_do_nothing(index_elements=[PendingMention.username])
        .returning(PendingMention.username)
    )
    return len((await session.execute(statement)).all())


async def pending_mentions_to_resolve(
    session: AsyncSession,
    *,
    retry_failed: bool = False,
    limit: int | None = None,
) -> Sequence[PendingMention]:
    """Pending usernames awaiting a public lookup.

    A username a previous run failed on is skipped by default and only
    retried under ``retry_failed``. Ordered oldest-first so a bounded run
    works through the backlog in the order it accrued.
    """
    statement = select(PendingMention)
    if not retry_failed:
        statement = statement.where(PendingMention.attempts == 0)
    statement = statement.order_by(
        PendingMention.first_seen_at, PendingMention.username
    )
    if limit is not None:
        statement = statement.limit(limit)
    return (await session.scalars(statement)).all()


async def record_pending_failure(
    session: AsyncSession, username: str, error: str
) -> None:
    """Count a failed resolution attempt on a pending username."""
    now = datetime.now(UTC)
    await session.execute(
        update(PendingMention)
        .where(PendingMention.username == username)
        .values(
            attempts=PendingMention.attempts + 1,
            last_attempt_at=now,
            last_error=error[:500],
        )
    )


async def delete_pending_mention(session: AsyncSession, username: str) -> None:
    """Drop a pending username once it has become a channel."""
    await session.execute(
        delete(PendingMention).where(PendingMention.username == username)
    )


async def truncate_derived(session: AsyncSession) -> None:
    """Empty the derived tables, and only those.

    The single path in the whole change that deletes derived data. It
    must never reach ``channels`` — a channel discovered by reference is
    kept, so a rebuild re-derives its edges rather than losing it — nor
    the raw layer, which is the source of truth a rebuild reads from.
    """
    await session.execute(text("TRUNCATE TABLE edges, pending_mentions"))
