"""The channel inventory: every read and write of ``channels``.

Records are never deleted here, and no import path may overwrite a
manual review.
"""

from collections.abc import Iterable, Sequence
from dataclasses import dataclass
from datetime import UTC, datetime

from sqlalchemy import Boolean, func, literal_column, select, update
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
    "AmbiguousUsernameError",
    "ChannelLookupError",
    "ChannelNotFoundError",
    "DiscoveredChannel",
    "UpsertCounts",
    "count_by_status",
    "find_channel",
    "link_discussion_chat",
    "list_channels",
    "mark_channel",
    "upsert_channels",
]

# A channel is addressed either by Telegram id or by username without
# the leading ``@``.
ChannelRef = int | str


def _spelled(ref: ChannelRef) -> str:
    """A reference as the operator typed it, for an error message."""
    return str(ref) if isinstance(ref, int) else f"@{ref}"


class ChannelLookupError(LookupError):
    """A reference did not resolve to exactly one channel."""


class ChannelNotFoundError(ChannelLookupError):
    """No channel with the given id or username is in the inventory."""

    def __init__(self, ref: ChannelRef) -> None:
        super().__init__(
            f"no channel {_spelled(ref)} in the inventory; "
            "run `itgraph dump-dialogs` first"
        )
        self.ref = ref


class AmbiguousUsernameError(ChannelLookupError):
    """Several channels carry the same username.

    Usernames are unique in Telegram at any one moment, but the
    inventory keeps a stale one until the next import: a channel that
    renames leaves its old username behind for whoever takes it next.
    """

    def __init__(self, username: str, tg_ids: Sequence[int]) -> None:
        listed = ", ".join(str(tg_id) for tg_id in tg_ids)
        super().__init__(
            f"@{username} matches {len(tg_ids)} channels ({listed}); "
            "give the id instead"
        )
        self.username = username
        self.tg_ids = tg_ids


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


async def link_discussion_chat(
    session: AsyncSession, *, parent_tg_id: int, chat: DiscoveredChannel
) -> None:
    """Record a channel's discussion chat, in the two steps it takes.

    ``upsert_channels`` refreshes only ``username`` and ``title`` on
    conflict, so it cannot carry ``linked_to`` — passing the column to it
    would be accepted and silently ignored. The chat row is therefore
    created or refreshed first, and the link written separately.

    A chat already imported from the operator's subscriptions keeps its
    original ``discovered_via`` and ``first_seen_at``; only ``linked_to``
    is written. Which is also why the link is not part of the upsert: a
    chat can be discovered either way round, and neither discovery may
    overwrite the other's record of where it came from.
    """
    await upsert_channels(
        session, [chat], discovered_via=DiscoverySource.LINKED_CHAT
    )
    await session.execute(
        update(Channel)
        .where(Channel.tg_id == chat.tg_id)
        .values(linked_to=parent_tg_id)
    )


async def find_channel(session: AsyncSession, ref: ChannelRef) -> Channel:
    """One channel by Telegram id, or by username without the ``@``.

    Usernames are matched case-insensitively, the way Telegram itself
    treats them. Raises ``ChannelNotFoundError`` when nothing matches
    and ``AmbiguousUsernameError`` when more than one row does.
    """
    if isinstance(ref, int):
        channel = await session.get(Channel, ref)
        if channel is None:
            raise ChannelNotFoundError(ref)
        return channel

    statement = (
        select(Channel)
        .where(func.lower(Channel.username) == ref.lower())
        .order_by(Channel.tg_id)
    )
    matches = (await session.scalars(statement)).all()
    if not matches:
        raise ChannelNotFoundError(ref)
    if len(matches) > 1:
        raise AmbiguousUsernameError(ref, [row.tg_id for row in matches])
    return matches[0]


async def mark_channel(
    session: AsyncSession,
    ref: ChannelRef,
    *,
    status: ChannelStatus,
    kind: ChannelKind | None = None,
    reject_reason: RejectReason | None = None,
    reject_note: str | None = None,
) -> Channel:
    """Record the review outcome for one channel.

    The channel is addressed by id or by username; see ``find_channel``
    for how a reference fails to resolve. Raises ``ValueError`` if a
    rejection carries no reason — the same rule the database enforces,
    reported before anything is written.
    """
    if (status is ChannelStatus.REJECTED) != (reject_reason is not None):
        raise ValueError(
            "a rejection needs a reason, and only a rejection may have one"
        )

    channel = await find_channel(session, ref)

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
