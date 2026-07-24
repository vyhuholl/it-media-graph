"""The derivation pass: raw messages in, edges out, no network at all.

This reads the database and writes the database. It never talks to
Telegram — that is ``itgraph resolve``'s job, and keeping the two apart
is what makes derivation fast, repeatable, and safe to run in a loop
while developing. A run over unchanged raw data writes nothing.

The shape of the pass follows from one asymmetry. A forward names a
channel by id, which is a primary key, so the endpoint row is created and
the edge written in the same batch. A mention names a channel by
``@username``, which is no id at all: if the inventory already knows the
username the edge is written now; if not, the username waits in
``pending_mentions`` for ``resolve`` to turn it into a channel, and the
*next* derivation run writes the edge. Mention edges therefore lag one
cycle behind forward edges, by construction.

An id-shaped link — ``t.me/c/<id>`` — is emitted as an edge only when the
inventory already holds that id. Unlike a forward header, which the API
itself produced, a bare id lifted from a link cannot be trusted to be a
channel at all, and creating an unverifiable row for it (that no cached
``access_hash`` could later resolve) buys a dead node. So an unknown
id-shaped link is dropped rather than discovered.
"""

import logging
from dataclasses import dataclass
from datetime import datetime
from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from itgraph.config import settings
from itgraph.db.edges import (
    ChannelIndex,
    EdgeRow,
    add_pending_mentions,
    create_discovered_channels,
    insert_edges,
    load_channel_index,
    truncate_derived,
)
from itgraph.db.models import DiscoverySource, EdgeKind, RawMessage
from itgraph.db.session import Database
from itgraph.derive.references import extract_references, forward_target

__all__ = ["DeriveSummary", "derive_graph"]

logger = logging.getLogger(__name__)


@dataclass(slots=True)
class DeriveSummary:
    """What a derivation run produced, in the terms it is judged by."""

    edges: int = 0
    discovered: int = 0
    pending: int = 0

    def line(self) -> str:
        return (
            f"{self.edges} edges written, {self.discovered} channels "
            f"discovered, {self.pending} mentions left pending"
        )


async def derive_graph(
    database: Database,
    *,
    batch_size: int | None = None,
    rebuild: bool = False,
) -> DeriveSummary:
    """Rebuild the edge graph from the raw layer.

    Streams ``raw_messages`` from one connection and writes edges on
    another, committing per batch. Two connections rather than one because
    a server-side cursor and the inserts it feeds cannot share a
    connection — and per batch so an interrupted run leaves a consistent
    prefix rather than all-or-nothing.

    ``rebuild`` truncates the derived tables first; without it the run is
    additive, and idempotent against what is already stored.
    """
    size = batch_size or settings.derive_batch_size
    summary = DeriveSummary()

    async with database.session() as writer:
        index = await load_channel_index(writer)
        if rebuild:
            await truncate_derived(writer)
            await writer.commit()

        async with database.session() as reader:
            result = await reader.stream(
                select(
                    RawMessage.channel_id,
                    RawMessage.msg_id,
                    RawMessage.payload,
                )
                .order_by(RawMessage.channel_id, RawMessage.msg_id)
                .execution_options(yield_per=size)
            )
            batch: list[Any] = []
            async for row in result:
                batch.append(row)
                if len(batch) >= size:
                    await _flush(writer, batch, index, summary)
                    batch = []
            if batch:
                await _flush(writer, batch, index, summary)

    logger.info("derivation done: %s", summary.line())
    return summary


async def _flush(
    writer: AsyncSession,
    rows: list[Any],
    index: ChannelIndex,
    summary: DeriveSummary,
) -> None:
    """Turn a batch of raw messages into channels, edges and pending rows.

    Endpoints are inserted before the edges that reference them, both in
    the one transaction the batch commits, so a killed process never
    leaves an edge pointing at a channel that does not exist.
    """
    discovered: set[int] = set()
    edge_rows: list[EdgeRow] = []
    pending: list[str] = []

    for channel_id, msg_id, payload in rows:
        published_at = _published_at(payload)
        if published_at is None:
            # No date, no edge: every time-decayed analysis reads it, and
            # a real message always carries one. Defensive, not expected.
            continue

        # A set keyed by (kind, dst): one message referencing the same
        # channel the same way twice yields one edge, but a forward and a
        # mention of that channel are two edges of two kinds.
        message_edges: set[tuple[EdgeKind, int]] = set()

        target = forward_target(payload, src_channel_id=channel_id)
        if target is not None:
            if target not in index.ids:
                discovered.add(target)
                index.add(target)
            message_edges.add((EdgeKind.FORWARD, target))

        for reference in extract_references(payload):
            if reference.username is not None:
                dst = index.username_to_id.get(reference.username)
                if dst is not None:
                    message_edges.add((EdgeKind.MENTION, dst))
                else:
                    pending.append(reference.username)
            elif (
                reference.channel_id is not None
                and reference.channel_id in index.ids
            ):
                message_edges.add((EdgeKind.MENTION, reference.channel_id))

        edge_rows.extend(
            EdgeRow(
                src_channel_id=channel_id,
                dst_channel_id=dst,
                kind=kind,
                msg_id=msg_id,
                published_at=published_at,
            )
            for kind, dst in message_edges
        )

    if discovered:
        summary.discovered += await create_discovered_channels(
            writer, tg_ids=discovered, discovered_via=DiscoverySource.FORWARD
        )
    if edge_rows:
        summary.edges += await insert_edges(writer, edge_rows)
    if pending:
        summary.pending += await add_pending_mentions(writer, pending)
    await writer.commit()


def _published_at(payload: dict[str, Any]) -> datetime | None:
    """The message's publication date, as stored (an ISO-8601 string)."""
    raw = payload.get("date")
    return datetime.fromisoformat(raw) if isinstance(raw, str) else None
