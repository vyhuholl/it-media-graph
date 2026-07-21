"""The channel inventory: every read and write of ``channels``.

Records are never deleted here, and no import path may overwrite a
manual review.
"""

from collections.abc import Iterable, Sequence
from dataclasses import dataclass
from datetime import UTC, datetime

from sqlalchemy import Boolean, func, literal_column, select
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.ext.asyncio import AsyncSession

from itgraph.db.models import (
    Channel,
    ChannelKind,
    ChannelStatus,
    DiscoverySource,
    RejectReason,
)

__all__ = [
    "ChannelNotFoundError",
    "DiscoveredChannel",
    "UpsertCounts",
    "count_by_status",
    "list_channels",
    "mark_channel",
    "upsert_channels",
]


class ChannelNotFoundError(LookupError):
    """No channel with the given Telegram id is in the inventory."""

    def __init__(self, tg_id: int) -> None:
        super().__init__(
            f"no channel {tg_id} in the inventory; "
            "run `itgraph dump-dialogs` first"
        )
        self.tg_id = tg_id


@dataclass(frozen=True, slots=True)
class DiscoveredChannel:
    """A channel's identity, as one discovery path saw it."""

    tg_id: int
    username: str | None
    title: str | None
    is_chat: bool


@dataclass(frozen=True, slots=True)
class UpsertCounts:
    """How an import split between new and already-known channels."""

    inserted: int
    updated: int


async def upsert_channels(
    session: AsyncSession,
    channels: Iterable[DiscoveredChannel],
    *,
    discovered_via: DiscoverySource,
) -> UpsertCounts:
    """Insert new channels, refresh the identity of known ones.

    First discovery wins: ``discovered_via``, ``first_seen_at`` and every
    review field of an existing row are left alone. Re-running an import
    must never cost the operator a review they already did.
    """
    # One row per id: Postgres refuses to let a single ON CONFLICT
    # statement touch the same row twice.
    rows = {
        channel.tg_id: {
            "tg_id": channel.tg_id,
            "username": channel.username,
            "title": channel.title,
            "is_chat": channel.is_chat,
            "discovered_via": discovered_via,
        }
        for channel in channels
    }
    if not rows:
        return UpsertCounts(inserted=0, updated=0)

    statement = insert(Channel).values(list(rows.values()))
    statement = statement.on_conflict_do_update(
        index_elements=[Channel.tg_id],
        set_={
            "username": statement.excluded.username,
            "title": statement.excluded.title,
        },
    )
    # xmax is zero only on a freshly inserted row; on the update path it
    # holds the id of the locking transaction. Cheaper, and racier by
    # nothing, than asking first which ids already existed.
    was_inserted = literal_column("xmax = 0", Boolean).label("inserted")
    flags: Sequence[bool] = (
        await session.scalars(statement.returning(was_inserted))
    ).all()
    inserted = sum(flags)
    return UpsertCounts(inserted=inserted, updated=len(flags) - inserted)


async def mark_channel(
    session: AsyncSession,
    tg_id: int,
    *,
    status: ChannelStatus,
    kind: ChannelKind | None = None,
    reject_reason: RejectReason | None = None,
    reject_note: str | None = None,
) -> Channel:
    """Record the review outcome for one channel.

    Raises ``ChannelNotFoundError`` for an unknown id and ``ValueError``
    if a rejection carries no reason — the same rule the database
    enforces, reported before anything is written.
    """
    if (status is ChannelStatus.REJECTED) != (reject_reason is not None):
        raise ValueError(
            "a rejection needs a reason, and only a rejection may have one"
        )

    channel = await session.get(Channel, tg_id)
    if channel is None:
        raise ChannelNotFoundError(tg_id)

    channel.status = status
    if kind is not None:
        channel.kind = kind
    # Clearing the old reason is what lets a rejected channel be marked
    # as a seed later without tripping the check constraint.
    channel.reject_reason = reject_reason
    channel.reject_note = reject_note if reject_reason is not None else None
    channel.reviewed_at = datetime.now(UTC)
    return channel


async def list_channels(
    session: AsyncSession, *, status: ChannelStatus | None = None
) -> Sequence[Channel]:
    """The inventory, optionally narrowed to one status."""
    statement = select(Channel).order_by(Channel.status, Channel.tg_id)
    if status is not None:
        statement = statement.where(Channel.status == status)
    return (await session.scalars(statement)).all()


async def count_by_status(session: AsyncSession) -> dict[ChannelStatus, int]:
    """How many channels sit at each status. Zeroes are included."""
    counts = dict.fromkeys(ChannelStatus, 0)
    statement = select(Channel.status, func.count()).group_by(Channel.status)
    for status, total in await session.execute(statement):
        counts[status] = total
    return counts
