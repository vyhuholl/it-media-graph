"""The journal of resolutions, and what the rising ranking reads.

The journal is one row per resolution — by ``itgraph resolve``,
``itgraph add`` or the poll loop — with when, which command, by id or by
username, and what it found. Never the username or the id: a name can be
a person's, and the journal is read for one thing, the cap on how many
resolutions the account spends in a rolling day.

The ranking's rows are read in one ``REPEATABLE READ`` transaction and
nothing is written: the ranking is worked out on reading and never
stored, by ``derive/rising.py``.
"""

from datetime import datetime

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from itgraph.db.affiliation import family_keys
from itgraph.db.channels import NOT_A_CHANNEL
from itgraph.db.models import (
    Channel,
    ChannelStatus,
    CollectionCommand,
    Edge,
    PendingMention,
    PendingMentionSource,
    Resolution,
    ResolutionKind,
    ResolutionOutcome,
)
from itgraph.derive.rising import (
    ChannelTarget,
    NameSource,
    PendingName,
    Reference,
    RisingRows,
)

__all__ = [
    "load_rising_rows",
    "newest_resolution",
    "record_resolution",
    "resolutions_since",
]

# The statuses a rising channel may have: not yet reviewed, or set aside.
# A seed is already in, and a rejected channel already out.
RISING_STATUSES = (ChannelStatus.CANDIDATE, ChannelStatus.MAYBE)


async def record_resolution(
    session: AsyncSession,
    command: CollectionCommand,
    kind: ResolutionKind,
    outcome: ResolutionOutcome,
) -> None:
    """Journal one resolution, in the caller's transaction.

    The caller's, because the row belongs with the answer it records: a
    resolution whose answer was rolled back was not one, as far as what
    is stored can tell.
    """
    session.add(Resolution(command=command, kind=kind, outcome=outcome))
    await session.flush()


async def resolutions_since(
    session: AsyncSession, moment: datetime
) -> dict[CollectionCommand, int]:
    """How many resolutions each command made after ``moment``.

    A command that made none is absent; the caller decides whether that
    reads as zero.
    """
    rows = await session.execute(
        select(Resolution.command, func.count())
        .where(Resolution.requested_at > moment)
        .group_by(Resolution.command)
    )
    return {command: count for command, count in rows.tuples()}


async def newest_resolution(
    session: AsyncSession, command: CollectionCommand
) -> datetime | None:
    """When ``command`` last resolved anything, from the journal.

    The poll loop's gap is read from here, so a restart does not reset it.
    """
    newest: datetime | None = await session.scalar(
        select(func.max(Resolution.requested_at)).where(
            Resolution.command == command
        )
    )
    return newest


async def load_rising_rows(session: AsyncSession) -> RisingRows:
    """Everything the ranking reads, as of one snapshot.

    The seeds that are not chats; every family; every edge from such a
    seed to a channel in ``candidate`` or ``maybe``, and those channels'
    identity state; every pending source from such a seed, with its
    times, and the names they mention that no channel answers to yet,
    with their attempts.

    Must be the first thing done in the session's transaction: the
    isolation level is set on it.
    """
    await session.connection(
        execution_options={"isolation_level": "REPEATABLE READ"}
    )
    # Subqueries rather than lists of ids: thousands of ids as parameters
    # would run into the protocol's limit on them.
    seed_ids = select(Channel.tg_id).where(
        Channel.status == ChannelStatus.SEED, Channel.is_chat.is_(False)
    )
    seeds = frozenset(await session.scalars(seed_ids))
    families = await family_keys(session)

    targets = select(Channel.tg_id).where(Channel.status.in_(RISING_STATUSES))
    from_seeds = Edge.src_channel_id.in_(seed_ids)
    edges = await session.execute(
        select(
            Edge.src_channel_id, Edge.dst_channel_id, Edge.published_at
        ).where(from_seeds, Edge.dst_channel_id.in_(targets))
    )
    references = [
        Reference(src=src, dst=dst, at=at) for src, dst, at in edges.tuples()
    ]

    referenced = select(Edge.dst_channel_id).where(from_seeds)
    channel_rows = await session.execute(
        select(
            Channel.tg_id,
            Channel.username,
            Channel.title,
            Channel.is_chat,
            Channel.resolved_at.is_not(None),
            Channel.resolve_attempts,
            Channel.resolve_last_error,
        ).where(
            Channel.status.in_(RISING_STATUSES),
            Channel.tg_id.in_(referenced),
        )
    )
    channels = {
        tg_id: ChannelTarget(
            tg_id=tg_id,
            username=username,
            title=title,
            is_chat=is_chat,
            resolved=resolved,
            attempts=attempts,
            not_channel=error == NOT_A_CHANNEL,
        )
        for (
            tg_id,
            username,
            title,
            is_chat,
            resolved,
            attempts,
            error,
        ) in channel_rows.tuples()
    }

    by_seeds = PendingMentionSource.channel_id.in_(seed_ids)
    source_rows = await session.execute(
        select(
            PendingMentionSource.username,
            PendingMentionSource.channel_id,
            PendingMentionSource.first_referenced_at,
            PendingMentionSource.last_referenced_at,
        ).where(by_seeds)
    )
    sources = [
        NameSource(username=name, src=src, first=first, last=last)
        for name, src, first, last in source_rows.tuples()
    ]

    # A name a channel already answers to is that channel's evidence, in
    # edges once derivation next runs; the resolution queue skips it too.
    # Case-insensitively: a pending name is stored normalised.
    name_rows = await session.execute(
        select(
            PendingMention.username,
            PendingMention.attempts,
            PendingMention.last_error,
        ).where(
            PendingMention.username.in_(
                select(PendingMentionSource.username).where(by_seeds)
            ),
            ~select(Channel.tg_id)
            .where(func.lower(Channel.username) == PendingMention.username)
            .exists(),
        )
    )
    names = {
        name: PendingName(
            username=name,
            attempts=attempts,
            not_channel=error == NOT_A_CHANNEL,
        )
        for name, attempts, error in name_rows.tuples()
    }

    return RisingRows(
        seeds=seeds,
        families=families,
        channels=channels,
        references=references,
        names=names,
        sources=sources,
    )
