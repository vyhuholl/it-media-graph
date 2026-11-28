"""One page of the history walk, as the poll loop takes it.

`itgraph backfill` asks for page after page, and its own
suite holds that. These hold what the loop relies on when it
takes one page, polls a dozen channels and comes back: one request a
call, everything read anew from the database, and the walk recorded
complete the moment nothing older is to be asked for.
"""

from collections.abc import AsyncIterator
from datetime import UTC, datetime, timedelta

import pytest
from fakes import FakeChannel, FakeTelegramClient, history
from sqlalchemy import select

from itgraph.db.backfill import advance_newest, load_state
from itgraph.db.channels import (
    DiscoveredChannel,
    mark_channel,
    upsert_channels,
)
from itgraph.db.models import (
    BackfillStatus,
    ChannelKind,
    ChannelStatus,
    CollectionCommand,
    DiscoverySource,
    RawMessage,
)
from itgraph.db.session import Database
from itgraph.tg import backfill as backfill_module
from itgraph.tg import pacing as pacing_module
from itgraph.tg.backfill import PageOutcome, walk_page
from itgraph.tg.floods import FloodRecorder

SEED = FakeChannel(1000000101, "example_new_seed", "Example New Seed")
CUTOFF = datetime(2026, 5, 1, tzinfo=UTC)


@pytest.fixture(autouse=True)
def no_sleeping(monkeypatch: pytest.MonkeyPatch) -> None:
    async def sleep(_seconds: float) -> None:
        return None

    monkeypatch.setattr(pacing_module.asyncio, "sleep", sleep)
    monkeypatch.setattr(backfill_module.asyncio, "sleep", sleep)


@pytest.fixture
async def seeded(database: Database) -> AsyncIterator[Database]:
    async with database.session() as session:
        await upsert_channels(
            session,
            [DiscoveredChannel(SEED.id, SEED.username, "Seed", False)],
            discovered_via=DiscoverySource.MANUAL,
        )
        await mark_channel(
            session,
            SEED.id,
            status=ChannelStatus.SEED,
            kind=ChannelKind.PERSONAL,
        )
    yield database


def telegram(
    count: int, *, newest: datetime | None = None
) -> FakeTelegramClient:
    return FakeTelegramClient(
        entities={SEED.username: SEED},
        histories={SEED.id: history(count, newest=newest)},
    )


async def page(
    database: Database,
    client: FakeTelegramClient,
    *,
    size: int = 2,
    max_messages: int | None = None,
    cutoff: datetime = CUTOFF,
) -> PageOutcome:
    recorder = FloodRecorder(database, CollectionCommand.WATCH).for_channel(
        SEED.id
    )
    async with database.session() as session:
        return await walk_page(
            client,
            session,
            channel_id=SEED.id,
            username=SEED.username,
            cutoff=cutoff,
            batch_size=size,
            request_delay=0,
            max_messages=max_messages,
            recorder=recorder,
        )


async def stored(database: Database) -> list[int]:
    async with database.session() as session:
        rows = await session.scalars(
            select(RawMessage.msg_id)
            .where(RawMessage.channel_id == SEED.id)
            .order_by(RawMessage.msg_id.desc())
        )
        return list(rows)


async def state_of(
    database: Database,
) -> tuple[BackfillStatus, datetime | None]:
    async with database.session() as session:
        state = await load_state(session, SEED.id)
        assert state is not None
        return state.status, state.cutoff_at


async def test_one_page_is_one_request(seeded: Database) -> None:
    client = telegram(5)

    outcome = await page(seeded, client)

    assert len(client.windows) == 1
    assert outcome == PageOutcome(stored=2)
    assert await stored(seeded) == [1000, 999]
    assert (await state_of(seeded))[0] is BackfillStatus.RUNNING


async def test_the_next_page_starts_below_the_last(seeded: Database) -> None:
    client = telegram(5)

    await page(seeded, client)
    await page(seeded, client)

    assert [offset for _, offset, _ in client.windows] == [0, 999]
    assert await stored(seeded) == [1000, 999, 998, 997]


async def test_a_short_window_ends_the_walk(seeded: Database) -> None:
    client = telegram(3)

    first = await page(seeded, client)
    last = await page(seeded, client)

    assert not first.done
    assert last == PageOutcome(stored=1, done=True)
    assert await state_of(seeded) == (BackfillStatus.COMPLETE, CUTOFF)


async def test_crossing_the_cutoff_ends_the_walk_at_the_cutoff(
    seeded: Database,
) -> None:
    # Two posts above the cutoff, then older ones the page leaves alone.
    client = telegram(5, newest=CUTOFF + timedelta(days=1, hours=12))

    outcome = await page(seeded, client, size=10)

    assert outcome == PageOutcome(stored=2, done=True)
    assert await stored(seeded) == [1000, 999]
    assert await state_of(seeded) == (BackfillStatus.COMPLETE, CUTOFF)


async def test_the_ceiling_ends_the_walk_at_the_oldest_post_stored(
    seeded: Database,
) -> None:
    client = telegram(10)

    first = await page(seeded, client, max_messages=3)
    last = await page(seeded, client, max_messages=3)

    assert not first.done
    # Asked for one, since one is all the ceiling left.
    assert client.windows[-1][2] == 1
    assert last == PageOutcome(stored=1, done=True, capped=True)
    oldest = history(10)[2]
    assert await state_of(seeded) == (BackfillStatus.COMPLETE, oldest.date)


async def test_a_channel_already_at_its_ceiling_is_not_asked(
    seeded: Database,
) -> None:
    client = telegram(5)
    await page(seeded, client)

    outcome = await page(seeded, client, max_messages=2)

    assert outcome == PageOutcome(done=True, capped=True, asked=False)
    assert len(client.windows) == 1
    assert (await state_of(seeded))[0] is BackfillStatus.RUNNING


async def test_a_poll_s_newest_mark_is_left_as_it_was(
    seeded: Database,
) -> None:
    """The poll owns the forward mark; a walk under way must not rewind it."""
    async with seeded.session() as session:
        await advance_newest(session, SEED.id, 5000)
        await session.commit()

    await page(seeded, telegram(5))

    async with seeded.session() as session:
        state = await load_state(session, SEED.id)
    assert state is not None
    assert state.newest_fetched_id == 5000
    assert state.oldest_fetched_id == 999
