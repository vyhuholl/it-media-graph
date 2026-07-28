"""Reads and writes of the derived tables: ``edges``, ``pending_mentions``
and the sources of those mentions.

All three are disposable — everything in them is recomputable from the raw
layer — which is what makes ``derive --rebuild`` able to truncate them
without a second thought. Nothing here touches ``channels`` beyond
creating rows a reference discovered, and nothing here reads the network.
"""

from collections.abc import Iterable, Sequence
from dataclasses import dataclass, field
from datetime import UTC, datetime

from sqlalchemy import Select, delete, func, select, text, update
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.ext.asyncio import AsyncSession

from itgraph.db.models import (
    Channel,
    DiscoverySource,
    Edge,
    EdgeKind,
    PendingMention,
    PendingMentionSource,
)

__all__ = [
    "ChannelIndex",
    "EdgeRow",
    "MentionSource",
    "QueuedMention",
    "add_pending_mentions",
    "count_pending_mention_sources",
    "create_discovered_channels",
    "delete_pending_mention",
    "insert_edges",
    "load_channel_index",
    "pending_mentions_to_resolve",
    "record_pending_failure",
    "truncate_derived",
]


@dataclass(frozen=True, slots=True)
class MentionSource:
    """One channel mentioning one username that is not yet a channel.

    A pair rather than a tally, because derivation has to stay
    re-runnable: a second pass over unchanged raw messages must write
    nothing, and an increment always writes.
    """

    channel_id: int
    username: str


@dataclass(frozen=True, slots=True)
class QueuedMention:
    """A username due a lookup, and how much evidence is behind it.

    Carries the count rather than leaving the caller to re-query it,
    because the count is what decided the order and a run should be able
    to say so in its log. Plain values rather than a mapped row: the
    caller commits between lookups, which expires mapped instances, and
    reading an attribute off an expired one is a lazy load an async
    session cannot perform.
    """

    username: str
    sources: int


@dataclass(frozen=True, slots=True)
class EdgeRow:
    """One edge, ready to insert. A plain carrier, not a model instance.

    ``dst_msg_id`` and ``dst_published_at`` name the referenced post where
    the payload does; ``grouped_id`` is the album group of the referencing
    message. All three are empty for a reference that names none.
    """

    src_channel_id: int
    dst_channel_id: int
    kind: EdgeKind
    msg_id: int
    published_at: datetime
    dst_msg_id: int | None = None
    dst_published_at: datetime | None = None
    grouped_id: int | None = None


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
    dst, dst_msg_id)`` makes a re-run over unchanged raw data a no-op: the
    second pass writes nothing. The conflict is named against the
    constraint rather than inferred from columns, because the constraint
    is ``NULLS NOT DISTINCT`` — a null ``dst_msg_id`` must conflict with
    another null, which is exactly the constraint's own rule and not
    something a bare column list would state. Returns how many rows were
    new.
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
                    "dst_msg_id": edge.dst_msg_id,
                    "dst_published_at": edge.dst_published_at,
                    "grouped_id": edge.grouped_id,
                }
                for edge in edges
            ]
        )
        .on_conflict_do_nothing(constraint="uq_edges_reference")
        .returning(Edge.id)
    )
    return len((await session.execute(statement)).all())


async def add_pending_mentions(
    session: AsyncSession, mentions: Iterable[MentionSource]
) -> int:
    """Record usernames mentioned but not yet known as channels, and who
    mentioned them.

    ``ON CONFLICT DO NOTHING`` on both inserts, which is what keeps
    derivation re-runnable: the pending row keeps its original
    ``first_seen_at`` and any resolution attempts already made against it,
    and a source pair already recorded is not recorded twice however many
    times the pass repeats.

    Returns how many usernames were newly pending — the sources are
    bookkeeping for the ordering, not work the operator asked about.

    The pending rows go in first because the sources have a foreign key
    onto them, and both are in the caller's one transaction.
    """
    pairs = {(item.username, item.channel_id) for item in mentions}
    if not pairs:
        return 0

    names = list(dict.fromkeys(username for username, _ in pairs))
    statement = (
        insert(PendingMention)
        .values([{"username": name} for name in names])
        .on_conflict_do_nothing(index_elements=[PendingMention.username])
        .returning(PendingMention.username)
    )
    inserted = len((await session.execute(statement)).all())

    sources = insert(PendingMentionSource).values(
        [
            {"username": username, "channel_id": channel_id}
            for username, channel_id in sorted(pairs)
        ]
    )
    await session.execute(
        sources.on_conflict_do_nothing(
            index_elements=[
                PendingMentionSource.username,
                PendingMentionSource.channel_id,
            ]
        )
    )
    return inserted


async def count_pending_mention_sources(session: AsyncSession) -> int:
    """How many source pairs are recorded at all.

    Asked once per resolution run to tell "nothing mentions these" apart
    from "derivation has not run since the sources table appeared". The
    two look identical in the ordering and mean opposite things.
    """
    total = await session.scalar(
        select(func.count()).select_from(PendingMentionSource)
    )
    return total or 0


async def pending_mentions_to_resolve(
    session: AsyncSession,
    *,
    retry_failed: bool = False,
    limit: int | None = None,
    min_sources: int | None = None,
) -> Sequence[QueuedMention]:
    """Pending usernames awaiting a public lookup, best evidence first.

    Ordered by how many distinct channels mention each username. Every row
    here costs one ``contacts.resolveUsername`` — the scarcest request in
    the project, a couple of hundred a day and no batch form — and the
    great majority of a real queue is mentioned by exactly one channel,
    which resolves into a vertex of degree one. Arrival order spends the
    day's quota on an arbitrary slice; this spends it on the references
    more than one channel thought worth making.

    ``first_seen_at`` survives as the tie-break, so a bounded run is still
    deterministic within a sitting. Across sittings the order *can* move —
    a later derivation may add a source and lift a username past others.
    That is the point rather than a defect, but it does mean this queue
    makes a weaker promise than the by-id one, which covers the same rows
    in the same order every time.

    ``min_sources`` bounds the queue by evidence where ``limit`` bounds it
    by budget; they compose. A username with no recorded sources counts as
    zero and sorts last.

    A username whose channel already exists is left out entirely. Those
    rows accumulate because the two resolution paths are asymmetric — a
    channel resolved by id gets a username without anything clearing the
    queue that was waiting on the same name — and requesting one can only
    return a channel the inventory already has. At the observed rate they
    were most of two days' quota spent to learn nothing.

    A username a previous run failed on is skipped by default and only
    retried under ``retry_failed``.
    """
    # Grouped once rather than correlated per row, so the filter and the
    # ordering read the same expression. The composite primary key already
    # makes the count distinct, so `COUNT(*)` needs no `DISTINCT`.
    sources = (
        select(
            PendingMentionSource.username.label("username"),
            func.count().label("total"),
        )
        .group_by(PendingMentionSource.username)
        .subquery()
    )
    total = func.coalesce(sources.c.total, 0)

    statement: Select[tuple[str, int]] = select(
        PendingMention.username, total.label("sources")
    ).outerjoin(sources, sources.c.username == PendingMention.username)
    # Case-insensitively: a pending username is stored normalised, a
    # channel's is stored the way Telegram spells it.
    statement = statement.where(
        ~select(Channel.tg_id)
        .where(func.lower(Channel.username) == PendingMention.username)
        .exists()
    )
    if not retry_failed:
        statement = statement.where(PendingMention.attempts == 0)
    if min_sources is not None:
        statement = statement.where(total >= min_sources)
    statement = statement.order_by(
        total.desc(), PendingMention.first_seen_at, PendingMention.username
    )
    if limit is not None:
        statement = statement.limit(limit)
    return [
        QueuedMention(username=username, sources=count)
        for username, count in await session.execute(statement)
    ]


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

    The three tables are named, not reached through ``CASCADE``. Postgres
    would accept ``CASCADE`` and follow every foreign key that ever points
    here, which is exactly the quiet reach the paragraph above forbids.
    """
    await session.execute(
        text("TRUNCATE TABLE edges, pending_mentions, pending_mention_sources")
    )
