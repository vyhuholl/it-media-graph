"""The channel inventory: every read and write of ``channels``.

Records are never deleted here, and no import path may overwrite a
manual review.
"""

from collections.abc import Iterable, Sequence
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any

from sqlalchemy import (
    BigInteger,
    Boolean,
    column,
    func,
    literal_column,
    select,
    table,
    update,
)
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.ext.asyncio import AsyncSession

from itgraph.affiliation.signals import Pair, ordered
from itgraph.db.affiliation import family_keys, family_of
from itgraph.db.models import (
    AffiliationCandidate,
    AffiliationDecision,
    CandidateOrigin,
    Channel,
    ChannelKind,
    ChannelStatus,
    DiscoverySource,
    RejectReason,
)

__all__ = [
    "AmbiguousUsernameError",
    "ChannelAlreadyResolvedError",
    "ChannelLookupError",
    "ChannelNotFoundError",
    "ChannelResolveFailedBeforeError",
    "ConfirmedGroup",
    "DiscoveredChannel",
    "FamilyCounts",
    "UpsertCounts",
    "channel_to_resolve",
    "channels_awaiting_resolution",
    "confirm_affiliation",
    "count_by_status",
    "count_families",
    "create_resolved_channel",
    "existing_usernames",
    "find_channel",
    "link_discussion_chat",
    "list_channels",
    "mark_channel",
    "record_channel_resolve_failure",
    "record_channel_resolved",
    "reject_affiliation",
    "upsert_channels",
    "withdraw_affiliation",
]

# A channel is addressed either by Telegram id or by username without
# the leading ``@``.
ChannelRef = int | str


def _spelled(ref: ChannelRef) -> str:
    """A reference as the operator typed it, for an error message."""
    return str(ref) if isinstance(ref, int) else f"@{ref}"


def _identity(channel: Channel) -> str:
    """A channel as it can be typed back into a command.

    Its username where it has one, its id otherwise — so an error that
    suggests a command suggests one that can be pasted rather than
    retyped from a listing.
    """
    return f"@{channel.username}" if channel.username else str(channel.tg_id)


def _families_subquery() -> Any:
    """The ``channel_families`` view, as something a query can join.

    Not a model: nothing maps to a view, and giving it one would invite
    `create_all` to try building it as a table. Declared inline instead,
    in the one place both readers of it need.
    """
    return (
        select(
            column("channel_id", BigInteger).label("channel_id"),
            column("family_key", BigInteger).label("family_key"),
        )
        .select_from(table("channel_families"))
        .subquery("families")
    )


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


class ChannelAlreadyResolvedError(ChannelLookupError):
    """A channel named for resolution already has an identity.

    Only reachable by naming one channel: a whole run answers this by
    leaving the row out of the queue, silently and correctly.
    """

    def __init__(self, channel: Channel) -> None:
        super().__init__(
            f"{_identity(channel)} is already resolved; "
            "there is nothing to ask Telegram about"
        )
        self.tg_id = channel.tg_id


class ChannelResolveFailedBeforeError(ChannelLookupError):
    """A channel named for resolution failed in an earlier run.

    Naming a channel says *which*, not *anyway* — the retry stays
    something the operator asks for, so the message names the flag that
    asks for it.
    """

    def __init__(self, channel: Channel) -> None:
        super().__init__(
            f"channel {channel.tg_id} failed to resolve before "
            f"({channel.resolve_last_error}); "
            "re-run with --retry-failed to try it again"
        )
        self.tg_id = channel.tg_id


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


@dataclass(frozen=True, slots=True)
class ConfirmedGroup:
    """What a confirmation established.

    ``pairs`` is what was written; ``channels`` is how large the family
    ended up. They differ whenever a confirmation bridged two families,
    which is now an ordinary outcome rather than a refusal — and the
    difference is exactly what the operator should see, so a merge is
    never silent.
    """

    pairs: int
    family: int
    channels: int


@dataclass(frozen=True, slots=True)
class FamilyCounts:
    """How many families, and how many channels sit in one."""

    families: int
    channels: int


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

    A channel imported with a username is known — it is born resolved, so
    it never enters the resolution queue. One imported without a username
    (a bare linked chat) is left awaiting resolution. On conflict the
    existing resolved state is preserved, and only filled in if a row
    discovered by reference is now, by this import, known by name.
    """
    now = datetime.now(UTC)
    # One row per id: Postgres refuses to let a single ON CONFLICT
    # statement touch the same row twice.
    rows = {
        channel.tg_id: {
            "tg_id": channel.tg_id,
            "username": channel.username,
            "title": channel.title,
            "is_chat": channel.is_chat,
            "discovered_via": discovered_via,
            "resolved_at": now if channel.username else None,
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
            "resolved_at": func.coalesce(
                Channel.resolved_at, statement.excluded.resolved_at
            ),
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


async def existing_usernames(
    session: AsyncSession, usernames: Sequence[str]
) -> set[str]:
    """Which of these usernames the inventory already holds, lowercased.

    One statement for the whole list, not one per name: a hand-written
    list of a hundred channels would otherwise be a hundred round trips
    to learn what a single query answers.

    Matched case-insensitively, the way :func:`find_channel` matches,
    because the inventory stores a username as Telegram spells it and a
    list typed by hand will not. Returned lowercased so the caller can
    compare against a parsed entry without normalising twice.

    Answers "is this username known", which is not quite "is this channel
    known": a channel discovered by forward and never resolved has an id
    and no username, so it is absent here and its row is found only by
    the write that lands on it.
    """
    if not usernames:
        return set()
    lowered = [name.lower() for name in usernames]
    statement = select(func.lower(Channel.username)).where(
        func.lower(Channel.username).in_(lowered)
    )
    return set((await session.scalars(statement)).all())


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
    session: AsyncSession,
    *,
    status: ChannelStatus | None = None,
    family: int | None = None,
) -> Sequence[Channel]:
    """The inventory, optionally narrowed to one status or one family.

    The family filter takes a *key*, not a member: resolve a channel
    reference to its family with :func:`family_keys` first. It joins the
    ``channel_families`` view rather than reading a column, and falls
    back to the channel's own id, so a channel in no family is its own
    family of one and comes back when asked for by itself.
    """
    families = _families_subquery()
    statement = (
        select(Channel)
        .outerjoin(families, families.c.channel_id == Channel.tg_id)
        .order_by(Channel.status, Channel.tg_id)
    )
    if status is not None:
        statement = statement.where(Channel.status == status)
    if family is not None:
        statement = statement.where(
            func.coalesce(families.c.family_key, Channel.tg_id) == family
        )
    return (await session.scalars(statement)).all()


async def count_families(session: AsyncSession) -> FamilyCounts:
    """How many families are recorded, and how many channels are in one.

    Counted over the view, so the numbers are families and memberships
    rather than pointers. A channel in no family is in neither count — it
    is its own family of one, which is true and not worth reporting.
    """
    families = _families_subquery()
    channels = (
        await session.scalar(select(func.count()).select_from(families))
    ) or 0
    distinct = (
        await session.scalar(
            select(func.count(func.distinct(families.c.family_key)))
        )
    ) or 0
    return FamilyCounts(families=distinct, channels=channels)


async def confirm_affiliation(
    session: AsyncSession,
    channels: Sequence[ChannelRef],
    *,
    note: str | None = None,
) -> ConfirmedGroup:
    """Record that these channels share an author.

    Two or more, and the statement is the same for any number of them:
    these are one author's. There is no canonical channel to name — a
    family is a set, and none of an author's channels is the main one.

    **Every pair among them is recorded, not a chain.** Four channels are
    six pairs. A chain would be three rows and a different claim:
    withdrawing its middle link would split a family the operator
    asserted as whole, and which pairs existed would depend on the order
    the channels were typed in.

    What used to live here and does not any more: the check that the
    canonical channel was one of the two, the depth-one rule, and the
    refusal when the sides were in different families. **Merging is now
    what confirming a bridging pair means**, and it needs no code — the
    row is written and the components join. Splitting needs none either;
    see :func:`withdraw_affiliation`.
    """
    resolved = [await find_channel(session, ref) for ref in channels]
    if len(resolved) < 2:
        raise ValueError("name at least two channels")

    ids = [channel.tg_id for channel in resolved]
    if len(set(ids)) != len(ids):
        raise ValueError(
            "a channel cannot be affiliated with itself; "
            "the same channel is named more than once"
        )

    pairs = [
        ordered(first, second)
        for index, first in enumerate(ids)
        for second in ids[index + 1 :]
    ]
    for pair in pairs:
        await _record_decision(
            session,
            pair=pair,
            decision=AffiliationDecision.CONFIRMED,
            note=note,
        )

    # Read the family back rather than predicting it: a bridging pair
    # merges whole families, so the group the operator named can be
    # smaller than the family they just created, and the difference is
    # the thing worth showing them.
    await session.flush()
    keys = await family_keys(session)
    key = family_of(keys, ids[0])
    return ConfirmedGroup(
        pairs=len(pairs),
        family=key,
        channels=sum(1 for value in keys.values() if value == key) or 1,
    )


async def reject_affiliation(
    session: AsyncSession,
    first: ChannelRef,
    second: ChannelRef,
    *,
    note: str | None = None,
) -> Pair:
    """Record that two channels do not share an author.

    A statement about a pair, which is why it takes exactly two: there is
    no reading of "reject this group" that says anything definite.

    The row is what stops the same pair being proposed at every
    subsequent run, which is the only reason a rejection is stored — so
    it is kept and never deleted. It says "this pair is not evidence",
    not "these two are not family": if other confirmed pairs connect them
    anyway, they stay in one family and the rejection keeps doing its
    only job.
    """
    left = await find_channel(session, first)
    right = await find_channel(session, second)
    if left.tg_id == right.tg_id:
        raise ValueError("a channel cannot be affiliated with itself")

    pair = ordered(left.tg_id, right.tg_id)
    await _record_decision(
        session,
        pair=pair,
        decision=AffiliationDecision.REJECTED,
        note=note,
    )
    return pair


async def withdraw_affiliation(
    session: AsyncSession, first: ChannelRef, second: ChannelRef
) -> Pair:
    """Undo a decision: the pair goes back to awaiting review.

    Nothing else happens, and nothing else needs to. The family is the
    connected components of the confirmed pairs, so removing one either
    changes nothing — because another chain still connects the two — or
    splits the family in exactly the place the withdrawn pair was
    holding together. Both outcomes fall out of the derivation rather
    than being implemented, which is most of the reason the family stopped
    being a stored pointer.
    """
    left = await find_channel(session, first)
    right = await find_channel(session, second)
    pair = ordered(left.tg_id, right.tg_id)

    await session.execute(
        update(AffiliationCandidate)
        .where(
            AffiliationCandidate.channel_a == pair[0],
            AffiliationCandidate.channel_b == pair[1],
        )
        .values(
            decision=AffiliationDecision.PENDING,
            decided_at=None,
            decision_note=None,
        )
    )
    return pair


async def _record_decision(
    session: AsyncSession,
    *,
    pair: Pair,
    decision: AffiliationDecision,
    note: str | None,
) -> None:
    """Write the review outcome onto the candidate, creating it if new.

    A pair no signal ever proposed is still recordable — the operator may
    simply know two channels share an author. Such a row is marked as
    having come from the operator rather than from a signal, so "how much
    did detection actually catch" stays answerable.
    """
    values = {
        "channel_a": pair[0],
        "channel_b": pair[1],
        "score": 0.0,
        "origin": CandidateOrigin.OPERATOR,
        "decision": decision,
        "decided_at": datetime.now(UTC),
        "decision_note": note,
    }
    statement = insert(AffiliationCandidate).values([values])
    await session.execute(
        statement.on_conflict_do_update(
            index_elements=[
                AffiliationCandidate.channel_a,
                AffiliationCandidate.channel_b,
            ],
            set_={
                "decision": statement.excluded.decision,
                "decided_at": statement.excluded.decided_at,
                "decision_note": statement.excluded.decision_note,
            },
        )
    )


async def channels_awaiting_resolution(
    session: AsyncSession,
    *,
    retry_failed: bool = False,
    limit: int | None = None,
    tg_id: int | None = None,
) -> Sequence[Channel]:
    """Channels that entered by reference and still lack an identity.

    The queue is ``resolved_at IS NULL``. A channel a previous run failed
    on — a cache miss, since resolving a bare id needs an ``access_hash``
    the session may not have yet — is skipped by default and only revisited
    under ``retry_failed``: a later backfill may have taught the session
    that hash, so the failure is provisional, not final.

    Ordered by id so a bounded run covers the same channels in the same
    order across sittings.

    ``tg_id`` narrows the queue to one named channel — an extra clause on
    this same predicate, deliberately not a lookup of its own. A named
    channel is therefore subject to the queue's rules rather than
    exempt from them: already resolved or failed-and-not-retried comes
    back empty here, the same way it goes unvisited in a whole run.
    """
    statement = select(Channel).where(Channel.resolved_at.is_(None))
    if not retry_failed:
        statement = statement.where(Channel.resolve_attempts == 0)
    if tg_id is not None:
        statement = statement.where(Channel.tg_id == tg_id)
    statement = statement.order_by(Channel.tg_id)
    if limit is not None:
        statement = statement.limit(limit)
    return (await session.scalars(statement)).all()


async def channel_to_resolve(
    session: AsyncSession,
    tg_id: int,
    *,
    retry_failed: bool = False,
) -> Channel:
    """The one channel a named run will resolve, or why it will not.

    Membership is :func:`channels_awaiting_resolution`'s to decide, and
    this asks it rather than re-stating its predicate. The lookup that
    follows an empty result exists only to write the sentence: absent
    from the inventory, resolved already, or failed before and not being
    retried are one empty result and three different things for the
    operator to do next.

    Raises a :class:`ChannelLookupError` for each of the three, which the
    CLI already turns into that sentence and exit 1.
    """
    queued = await channels_awaiting_resolution(
        session, retry_failed=retry_failed, tg_id=tg_id
    )
    if queued:
        return queued[0]

    # Raises `ChannelNotFoundError` when the inventory holds no such row.
    channel = await find_channel(session, tg_id)
    if channel.resolved_at is not None:
        raise ChannelAlreadyResolvedError(channel)
    # What is left: in the queue's terms, a past failure not being
    # retried. Under `retry_failed` there is no third case — the row
    # would have come back above.
    raise ChannelResolveFailedBeforeError(channel)


async def record_channel_resolved(
    session: AsyncSession,
    tg_id: int,
    *,
    username: str | None,
    title: str | None,
    is_chat: bool,
) -> None:
    """Store a resolved identity and stamp the channel resolved.

    Setting ``resolved_at`` is what takes the row out of the resolution
    queue and — now that it has a username — puts it into the review one.
    """
    now = datetime.now(UTC)
    await session.execute(
        update(Channel)
        .where(Channel.tg_id == tg_id)
        .values(
            username=username,
            title=title,
            is_chat=is_chat,
            resolved_at=now,
            resolve_last_attempt_at=now,
            resolve_last_error=None,
        )
    )


async def record_channel_resolve_failure(
    session: AsyncSession, tg_id: int, error: str
) -> None:
    """Count a failed attempt and store why, without resolving the row.

    The row keeps ``resolved_at IS NULL``, so it stays out of the review
    queue; the bumped attempt count keeps it out of routine resolution
    until ``retry_failed`` asks for it.
    """
    now = datetime.now(UTC)
    await session.execute(
        update(Channel)
        .where(Channel.tg_id == tg_id)
        .values(
            resolve_attempts=Channel.resolve_attempts + 1,
            resolve_last_attempt_at=now,
            # Truncated: a driver traceback in a status column helps
            # nobody, and the log has the whole thing.
            resolve_last_error=error[:500],
        )
    )


async def create_resolved_channel(
    session: AsyncSession,
    *,
    channel: DiscoveredChannel,
    discovered_via: DiscoverySource,
) -> bool:
    """Create — or refresh — a channel whose identity is already known.

    Used when a pending mention resolves: the lookup returned a full
    identity, so the new row is stamped resolved at once rather than
    queued to resolve itself. A channel that already exists (discovered by
    an earlier forward, say) keeps its provenance, status and first-seen
    time; only its identity and resolved state are refreshed.

    Returns whether the row was created. A caller that may review what it
    adds needs that answer and cannot get it beforehand: a channel known
    only by id carries no username to look for, so "not in the inventory
    by name" and "not in the inventory" are different questions and only
    the write knows which one was being asked.
    """
    now = datetime.now(UTC)
    statement = insert(Channel).values(
        tg_id=channel.tg_id,
        username=channel.username,
        title=channel.title,
        is_chat=channel.is_chat,
        discovered_via=discovered_via,
        resolved_at=now,
        resolve_last_attempt_at=now,
    )
    # `xmax = 0` distinguishes the insert path from the update path; see
    # `upsert_channels`, which reads it the same way.
    was_inserted = literal_column("xmax = 0", Boolean).label("inserted")
    inserted = await session.scalar(
        statement.on_conflict_do_update(
            index_elements=[Channel.tg_id],
            set_={
                "username": statement.excluded.username,
                "title": statement.excluded.title,
                "is_chat": statement.excluded.is_chat,
                "resolved_at": statement.excluded.resolved_at,
                "resolve_last_attempt_at": statement.excluded.resolve_last_attempt_at,
                "resolve_last_error": None,
            },
        ).returning(was_inserted)
    )
    return bool(inserted)


async def count_by_status(session: AsyncSession) -> dict[ChannelStatus, int]:
    """How many channels sit at each status. Zeroes are included."""
    counts = dict.fromkeys(ChannelStatus, 0)
    statement = select(Channel.status, func.count()).group_by(Channel.status)
    for status, total in await session.execute(statement):
        counts[status] = total
    return counts
