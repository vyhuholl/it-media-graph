"""Which seeds the poll loop still has to walk, and in what order.

The predicate is the one the series counts by, so what is held here is
also what the monitoring is told.
"""

from datetime import UTC, datetime, timedelta

from sqlalchemy import update

from itgraph.db.backfill import (
    advance_newest,
    record_complete,
    record_failure,
    record_skip,
    seeds_awaiting_walk,
    start_channel,
)
from itgraph.db.channels import (
    DiscoveredChannel,
    mark_channel,
    upsert_channels,
)
from itgraph.db.models import (
    Channel,
    ChannelKind,
    ChannelStatus,
    DiscoverySource,
    FailureKind,
    RejectReason,
)
from itgraph.db.session import Database

CUTOFF = datetime(2025, 7, 24, tzinfo=UTC)
LATER = datetime(2026, 3, 1, tzinfo=UTC)

# Fake ids, one per case.
NEVER = 1000000201
PENDING = 1000000202
RUNNING = 1000000203
SKIPPED = 1000000204
TRANSIENT = 1000000205
PERMANENT = 1000000206
COMPLETE = 1000000207
COMPLETE_LATER = 1000000208
REJECTED = 1000000209
CANDIDATE = 1000000210
CHAT = 1000000211


async def add(
    database: Database,
    tg_id: int,
    *,
    status: ChannelStatus = ChannelStatus.SEED,
    is_chat: bool = False,
) -> None:
    async with database.session() as session:
        await upsert_channels(
            session,
            [DiscoveredChannel(tg_id, f"fake_{tg_id}", "Fake", is_chat)],
            discovered_via=DiscoverySource.MANUAL,
        )
        if status is ChannelStatus.SEED:
            await mark_channel(
                session, tg_id, status=status, kind=ChannelKind.PERSONAL
            )
        elif status is ChannelStatus.REJECTED:
            await mark_channel(
                session,
                tg_id,
                status=status,
                reject_reason=RejectReason.NOT_IT,
            )


async def awaiting(database: Database) -> list[int]:
    async with database.session() as session:
        return [seed.tg_id for seed in await seeds_awaiting_walk(session)]


async def test_every_walk_not_complete_awaits(database: Database) -> None:
    for tg_id in (
        NEVER,
        PENDING,
        RUNNING,
        SKIPPED,
        TRANSIENT,
        PERMANENT,
        COMPLETE,
        COMPLETE_LATER,
    ):
        await add(database, tg_id)
    async with database.session() as session:
        await advance_newest(session, PENDING, 5000)
        await start_channel(session, RUNNING)
        await record_skip(session, SKIPPED, "no cached peer")
        await record_failure(
            session, TRANSIENT, FailureKind.TRANSIENT, "ConnectionError"
        )
        await record_failure(
            session, PERMANENT, FailureKind.PERMANENT, "ChannelPrivateError"
        )
        await record_complete(session, COMPLETE, CUTOFF)
        # Stopped at the ceiling, or walked to a later `--since`: done.
        await record_complete(session, COMPLETE_LATER, LATER)
        await session.commit()

    assert sorted(await awaiting(database)) == [
        NEVER,
        PENDING,
        RUNNING,
        SKIPPED,
        TRANSIENT,
    ]


async def test_only_seeds_that_are_not_chats_await(database: Database) -> None:
    await add(database, NEVER)
    await add(database, REJECTED, status=ChannelStatus.REJECTED)
    await add(database, CANDIDATE, status=ChannelStatus.CANDIDATE)
    await add(database, CHAT, is_chat=True)

    assert await awaiting(database) == [NEVER]


async def test_the_seed_marked_earliest_comes_first(
    database: Database,
) -> None:
    for tg_id in (NEVER, PENDING, RUNNING):
        await add(database, tg_id)
    now = datetime.now(UTC)
    marked = {
        NEVER: now - timedelta(hours=1),
        PENDING: now - timedelta(hours=3),
        RUNNING: now - timedelta(hours=3),
    }
    async with database.session() as session:
        for tg_id, at in marked.items():
            await session.execute(
                update(Channel)
                .where(Channel.tg_id == tg_id)
                .values(reviewed_at=at)
            )
        await session.commit()

    # Marked together, the lower id first; marked later, last.
    assert await awaiting(database) == [PENDING, RUNNING, NEVER]
