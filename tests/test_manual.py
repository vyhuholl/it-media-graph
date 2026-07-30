"""Adding channels by name — the pass that spends a lookup and no join.

Telethon is a fake and sleeps are recorded, not taken. What is under
test is what the run spends and what it writes: which names cost a
request, which cost nothing, and what each answer does to the inventory.
"""

from collections.abc import AsyncIterator

import pytest
from fakes import FakeTelegramClient, tl_channel, tl_user
from sqlalchemy import select

from itgraph.db.channels import (
    DiscoveredChannel,
    mark_channel,
    upsert_channels,
)
from itgraph.db.edges import MentionSource, add_pending_mentions
from itgraph.db.floods import store_flood_event
from itgraph.db.models import (
    Channel,
    ChannelKind,
    ChannelStatus,
    CollectionCommand,
    DiscoverySource,
    FloodEvent,
    PendingMention,
    RejectReason,
)
from itgraph.db.session import Database
from itgraph.tg import backfill as backfill_module
from itgraph.tg import pacing as pacing_module
from itgraph.tg.manual import Review, add_channels

SRC = 1000000001
NEW = 3000000001
OTHER = 3000000002


@pytest.fixture
def slept(monkeypatch: pytest.MonkeyPatch) -> list[float]:
    """Record sleeps instead of taking them, on both paced paths."""
    taken: list[float] = []

    async def sleep(seconds: float) -> None:
        taken.append(seconds)

    monkeypatch.setattr(pacing_module.asyncio, "sleep", sleep)
    monkeypatch.setattr(backfill_module.asyncio, "sleep", sleep)
    return taken


@pytest.fixture
def no_long_pauses(monkeypatch: pytest.MonkeyPatch) -> None:
    """Switch off the rare long pause, so a band assertion is decidable."""
    from itgraph.config import settings

    monkeypatch.setattr(settings, "pacing_long_pause_chance", 0.0)


@pytest.fixture
async def inventory(database: Database) -> AsyncIterator[Database]:
    """One channel already in the inventory, reviewed."""
    async with database.session() as session:
        await upsert_channels(
            session,
            [DiscoveredChannel(SRC, "known_channel", "Known", False)],
            discovered_via=DiscoverySource.OWN_SUBSCRIPTIONS,
        )
        await mark_channel(
            session,
            SRC,
            status=ChannelStatus.SEED,
            kind=ChannelKind.PERSONAL,
        )
    yield database


async def channel(database: Database, tg_id: int) -> Channel | None:
    async with database.session() as session:
        return await session.get(Channel, tg_id)


# --- what a name becomes ---------------------------------------------


async def test_a_username_becomes_a_channel(
    inventory: Database, slept: list[float]
) -> None:
    client = FakeTelegramClient(
        entities={
            "fake_new": tl_channel(NEW, username="fake_new", title="New")
        }
    )

    summary = await add_channels(
        client, inventory, usernames=["fake_new"], delay=0
    )

    assert summary.added == 1
    added = await channel(inventory, NEW)
    assert added is not None
    assert added.username == "fake_new"
    assert added.title == "New"
    assert added.discovered_via is DiscoverySource.MANUAL
    assert added.status is ChannelStatus.CANDIDATE
    assert added.resolved_at is not None


async def test_nothing_is_joined_and_no_dialog_list_is_read(
    inventory: Database, slept: list[float]
) -> None:
    """The whole reason this command exists rather than a subscription."""
    client = FakeTelegramClient(
        records=[
            {
                "id": 999,
                "title": "Should never be imported",
                "username": "not_asked_for",
                "type": "channel",
            }
        ],
        entities={"fake_new": tl_channel(NEW, username="fake_new")},
    )

    await add_channels(client, inventory, usernames=["fake_new"], delay=0)

    # No join request of any kind, and the dialog list untouched.
    assert client.requests == []
    async with inventory.session() as session:
        rows = (await session.scalars(select(Channel.username))).all()
    assert "not_asked_for" not in rows


async def test_a_user_creates_nothing(
    inventory: Database, slept: list[float]
) -> None:
    client = FakeTelegramClient(entities={"fake_person": tl_user(777)})

    summary = await add_channels(
        client, inventory, usernames=["fake_person"], delay=0
    )

    assert summary.added == 0
    assert summary.not_channels == 1
    async with inventory.session() as session:
        assert (await session.scalars(select(Channel.tg_id))).all() == [SRC]


async def test_a_bot_creates_nothing(
    inventory: Database, slept: list[float]
) -> None:
    client = FakeTelegramClient(entities={"fake_bot": tl_user(778, bot=True)})

    summary = await add_channels(
        client, inventory, usernames=["fake_bot"], delay=0
    )

    assert summary.not_channels == 1


async def test_a_failed_lookup_is_reported_and_the_run_continues(
    inventory: Database, slept: list[float]
) -> None:
    client = FakeTelegramClient(
        entities={"fake_new": tl_channel(NEW, username="fake_new")}
    )

    summary = await add_channels(
        client, inventory, usernames=["fake_missing", "fake_new"], delay=0
    )

    assert summary.added == 1
    assert [name for name, _ in summary.failures] == ["fake_missing"]
    assert await channel(inventory, NEW) is not None


# --- what costs a request, and what does not -------------------------


async def test_a_known_username_costs_no_request(
    inventory: Database, slept: list[float]
) -> None:
    client = FakeTelegramClient(entities={})

    summary = await add_channels(
        client, inventory, usernames=["known_channel"], delay=0
    )

    assert client.resolved == []
    assert summary.known == 1
    assert summary.added == 0


async def test_a_known_username_is_matched_case_insensitively(
    inventory: Database, slept: list[float]
) -> None:
    """Telegram spells a username how it likes; a typed list does not.

    The entry arrives lowercased — parsing does that — so the case that
    has to match is the one the *inventory* holds.
    """
    async with inventory.session() as session:
        await upsert_channels(
            session,
            [DiscoveredChannel(OTHER, "Fake_Capitalised", "Caps", False)],
            discovered_via=DiscoverySource.OWN_SUBSCRIPTIONS,
        )
    client = FakeTelegramClient(entities={})

    summary = await add_channels(
        client, inventory, usernames=["fake_capitalised"], delay=0
    )

    assert summary.known == 1
    assert client.resolved == []


async def test_a_rerun_over_the_same_list_resolves_only_the_remainder(
    inventory: Database, slept: list[float]
) -> None:
    """The resume mechanism: the inventory is the progress marker."""
    entities = {
        "fake_new": tl_channel(NEW, username="fake_new"),
        "fake_other": tl_channel(OTHER, username="fake_other"),
    }
    names = ["fake_new", "fake_other"]

    first = FakeTelegramClient(entities=entities)
    await add_channels(first, inventory, usernames=names, delay=0, limit=1)
    assert first.resolved == ["fake_new"]

    second = FakeTelegramClient(entities=entities)
    summary = await add_channels(second, inventory, usernames=names, delay=0)

    assert second.resolved == ["fake_other"]
    assert summary.added == 1
    assert summary.known == 1


async def test_a_limit_bounds_lookups_not_entries(
    inventory: Database, slept: list[float]
) -> None:
    """A name already held must not consume the budget."""
    client = FakeTelegramClient(
        entities={
            "fake_new": tl_channel(NEW, username="fake_new"),
            "fake_other": tl_channel(OTHER, username="fake_other"),
        }
    )

    summary = await add_channels(
        client,
        inventory,
        usernames=["known_channel", "fake_new", "fake_other"],
        delay=0,
        limit=2,
    )

    assert client.resolved == ["fake_new", "fake_other"]
    assert summary.added == 2
    assert summary.known == 1


async def test_a_limit_stops_the_run_short(
    inventory: Database, slept: list[float]
) -> None:
    client = FakeTelegramClient(
        entities={
            "fake_new": tl_channel(NEW, username="fake_new"),
            "fake_other": tl_channel(OTHER, username="fake_other"),
        }
    )

    summary = await add_channels(
        client,
        inventory,
        usernames=["fake_new", "fake_other"],
        delay=0,
        limit=1,
    )

    assert client.resolved == ["fake_new"]
    assert summary.added == 1


# --- reviewing on the way in -----------------------------------------


async def test_a_review_lands_on_a_channel_this_run_created(
    inventory: Database, slept: list[float]
) -> None:
    client = FakeTelegramClient(
        entities={"fake_new": tl_channel(NEW, username="fake_new")}
    )

    await add_channels(
        client,
        inventory,
        usernames=["fake_new"],
        delay=0,
        review=Review(status=ChannelStatus.SEED, kind=ChannelKind.MEDIA),
    )

    added = await channel(inventory, NEW)
    assert added is not None
    assert added.status is ChannelStatus.SEED
    assert added.kind is ChannelKind.MEDIA
    assert added.reviewed_at is not None


async def test_an_existing_review_is_never_overwritten(
    inventory: Database, slept: list[float]
) -> None:
    """A row already reviewed keeps its judgement, whatever is asked for.

    The case that would bite: a channel rejected last week, still named
    in the list, and a run that would silently un-reject it.
    """
    async with inventory.session() as session:
        await mark_channel(
            session,
            SRC,
            status=ChannelStatus.REJECTED,
            reject_reason=RejectReason.NOT_IT,
        )
    # Known by a *different* handle, so the skip set misses it and the
    # write is what discovers the row already exists.
    client = FakeTelegramClient(
        entities={"fake_renamed": tl_channel(SRC, username="fake_renamed")}
    )

    summary = await add_channels(
        client,
        inventory,
        usernames=["fake_renamed"],
        delay=0,
        review=Review(status=ChannelStatus.SEED, kind=ChannelKind.MEDIA),
    )

    existing = await channel(inventory, SRC)
    assert existing is not None
    assert existing.status is ChannelStatus.REJECTED
    assert existing.reject_reason is RejectReason.NOT_IT
    # Identity refreshed, judgement untouched.
    assert existing.username == "fake_renamed"
    assert summary.added == 0
    assert summary.known == 1


async def test_provenance_of_an_existing_channel_is_kept(
    inventory: Database, slept: list[float]
) -> None:
    client = FakeTelegramClient(
        entities={"fake_renamed": tl_channel(SRC, username="fake_renamed")}
    )

    await add_channels(client, inventory, usernames=["fake_renamed"], delay=0)

    existing = await channel(inventory, SRC)
    assert existing is not None
    assert existing.discovered_via is DiscoverySource.OWN_SUBSCRIPTIONS


# --- the mention queue -----------------------------------------------


async def test_a_pending_mention_the_addition_makes_redundant_is_cleared(
    inventory: Database, slept: list[float]
) -> None:
    async with inventory.session() as session:
        await add_pending_mentions(
            session, [MentionSource(channel_id=SRC, username="fake_new")]
        )
    client = FakeTelegramClient(
        entities={"fake_new": tl_channel(NEW, username="fake_new")}
    )

    await add_channels(client, inventory, usernames=["fake_new"], delay=0)

    async with inventory.session() as session:
        assert await session.get(PendingMention, "fake_new") is None


# --- collection limits ------------------------------------------------


async def test_requests_are_paced(
    inventory: Database, slept: list[float], no_long_pauses: None
) -> None:
    client = FakeTelegramClient(
        entities={
            "fake_new": tl_channel(NEW, username="fake_new"),
            "fake_other": tl_channel(OTHER, username="fake_other"),
        }
    )

    await add_channels(
        client, inventory, usernames=["fake_new", "fake_other"], delay=4.0
    )

    assert len(slept) == 2
    assert all(2.0 <= gap <= 6.0 for gap in slept)


async def test_a_short_flood_wait_is_slept_off_and_the_lookup_retried(
    inventory: Database, slept: list[float]
) -> None:
    client = FakeTelegramClient(
        entities={"fake_new": tl_channel(NEW, username="fake_new")},
        resolve_floods={"fake_new": 30},
    )

    summary = await add_channels(
        client, inventory, usernames=["fake_new"], delay=0
    )

    assert 30 in slept
    assert summary.added == 1


async def test_a_long_flood_wait_halts_the_run(
    inventory: Database, slept: list[float]
) -> None:
    """A day-long wait stops the run rather than being slept through."""
    client = FakeTelegramClient(
        entities={
            "fake_new": tl_channel(NEW, username="fake_new"),
            "fake_other": tl_channel(OTHER, username="fake_other"),
        },
        resolve_floods={"fake_other": 86400},
    )

    summary = await add_channels(
        client, inventory, usernames=["fake_new", "fake_other"], delay=0
    )

    assert summary.halt is not None
    assert summary.halt.seconds == 86400
    assert 86400 not in slept
    # What was added before the halt is committed.
    assert summary.added == 1
    assert await channel(inventory, NEW) is not None


async def test_a_rate_limit_is_recorded_against_this_command(
    inventory: Database, slept: list[float]
) -> None:
    client = FakeTelegramClient(
        entities={"fake_new": tl_channel(NEW, username="fake_new")},
        resolve_floods={"fake_new": 30},
    )

    await add_channels(client, inventory, usernames=["fake_new"], delay=0)

    async with inventory.session() as session:
        events = (await session.scalars(select(FloodEvent))).all()
    assert len(events) == 1
    assert events[0].command is CollectionCommand.ADD
    assert events[0].seconds == 30


# --- the warning ------------------------------------------------------


async def test_a_recent_limit_on_this_method_is_reported(
    inventory: Database,
    slept: list[float],
    caplog: pytest.LogCaptureFixture,
) -> None:
    async with inventory.session() as session:
        await store_flood_event(
            session,
            method="ResolveUsernameRequest",
            seconds=86400,
            command=CollectionCommand.RESOLVE,
            channel_id=None,
            halted=True,
        )
    client = FakeTelegramClient(
        entities={"fake_new": tl_channel(NEW, username="fake_new")}
    )

    with caplog.at_level("WARNING"):
        summary = await add_channels(
            client, inventory, usernames=["fake_new"], delay=0
        )

    assert "ResolveUsernameRequest" in caplog.text
    assert "resolve" in caplog.text
    # Reported, not obeyed: the run proceeds.
    assert summary.added == 1


async def test_no_recent_limit_is_reported_when_there_is_none(
    inventory: Database,
    slept: list[float],
    caplog: pytest.LogCaptureFixture,
) -> None:
    client = FakeTelegramClient(
        entities={"fake_new": tl_channel(NEW, username="fake_new")}
    )

    with caplog.at_level("WARNING"):
        await add_channels(client, inventory, usernames=["fake_new"], delay=0)

    assert "rate-limited" not in caplog.text


# --- durability of what the lookup learned ----------------------------


async def test_adding_a_channel_commits_the_session(
    inventory: Database, slept: list[float]
) -> None:
    """The access_hash has to outlive the process, not just the run.

    Telethon commits learned entities once a minute and on disconnect;
    a channel recorded as added while the session never committed its
    peer is one `backfill` skips forever.
    """
    client = FakeTelegramClient(
        entities={"fake_new": tl_channel(NEW, username="fake_new")}
    )

    await add_channels(client, inventory, usernames=["fake_new"], delay=0)

    assert client.session.saves == 1


async def test_the_session_is_committed_before_the_database(
    inventory: Database, slept: list[float], monkeypatch: pytest.MonkeyPatch
) -> None:
    """The ordering is the point, not the saving.

    Session first: a crash in between leaves a warm session and a row
    that was never marked resolved, which the next run simply redoes.
    The other order is what strands a channel.
    """
    from sqlalchemy.ext.asyncio import AsyncSession

    async def failing_commit(self: AsyncSession) -> None:
        raise RuntimeError("database went away")

    monkeypatch.setattr(AsyncSession, "commit", failing_commit)
    client = FakeTelegramClient(
        entities={"fake_new": tl_channel(NEW, username="fake_new")}
    )

    with pytest.raises(RuntimeError, match="database went away"):
        await add_channels(client, inventory, usernames=["fake_new"], delay=0)

    # The peer was durable before the write that would have claimed it.
    assert client.session.saves == 1


async def test_a_failed_lookup_commits_nothing(
    inventory: Database, slept: list[float]
) -> None:
    """Nothing was learned, so there is nothing to make durable."""
    client = FakeTelegramClient(entities={})

    await add_channels(client, inventory, usernames=["fake_missing"], delay=0)

    assert client.session.saves == 0
