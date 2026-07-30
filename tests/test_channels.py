"""Manual review of the inventory."""

import pytest
from sqlalchemy import text
from sqlalchemy.exc import IntegrityError

from itgraph.db.channels import (
    AmbiguousUsernameError,
    ChannelNotFoundError,
    DiscoveredChannel,
    count_by_status,
    create_resolved_channel,
    existing_usernames,
    find_channel,
    list_channels,
    mark_channel,
    upsert_channels,
)
from itgraph.db.models import (
    Channel,
    ChannelKind,
    ChannelStatus,
    DiscoverySource,
    RejectReason,
)
from itgraph.db.session import Database

KNOWN = 1000000001
UNKNOWN = 999999999


async def seed_inventory(database: Database, count: int = 3) -> None:
    async with database.session() as session:
        await upsert_channels(
            session,
            [
                DiscoveredChannel(
                    tg_id=KNOWN + offset,
                    username=f"example_{offset}",
                    title=f"Example {offset}",
                    is_chat=False,
                )
                for offset in range(count)
            ],
            discovered_via=DiscoverySource.OWN_SUBSCRIPTIONS,
        )


async def test_accepting_a_channel(database: Database) -> None:
    await seed_inventory(database)

    async with database.session() as session:
        await mark_channel(session, KNOWN, status=ChannelStatus.SEED)
        channel = await session.get(Channel, KNOWN)

    assert channel is not None
    assert channel.status is ChannelStatus.SEED
    assert channel.reviewed_at is not None


async def test_accepting_with_an_explicit_kind(database: Database) -> None:
    await seed_inventory(database)

    async with database.session() as session:
        channel = await mark_channel(
            session,
            KNOWN,
            status=ChannelStatus.SEED,
            kind=ChannelKind.VACANCIES,
        )

    assert channel.kind is ChannelKind.VACANCIES


async def test_rejecting_a_channel(database: Database) -> None:
    await seed_inventory(database)

    async with database.session() as session:
        channel = await mark_channel(
            session,
            KNOWN,
            status=ChannelStatus.REJECTED,
            reject_reason=RejectReason.INFOBIZ,
            reject_note="course funnel",
        )

    assert channel.status is ChannelStatus.REJECTED
    assert channel.reject_reason is RejectReason.INFOBIZ
    assert channel.reject_note == "course funnel"
    assert channel.reviewed_at is not None


async def test_deferring_a_decision(database: Database) -> None:
    await seed_inventory(database)

    async with database.session() as session:
        channel = await mark_channel(
            session, KNOWN, status=ChannelStatus.MAYBE
        )

    assert channel.status is ChannelStatus.MAYBE
    assert channel.reviewed_at is not None


async def test_rejecting_without_a_reason_fails(database: Database) -> None:
    await seed_inventory(database)

    with pytest.raises(ValueError):
        async with database.session() as session:
            await mark_channel(session, KNOWN, status=ChannelStatus.REJECTED)

    async with database.session() as session:
        channel = await session.get(Channel, KNOWN)

    assert channel is not None
    assert channel.status is ChannelStatus.CANDIDATE
    assert channel.reviewed_at is None


async def test_a_reason_without_a_rejection_fails(database: Database) -> None:
    await seed_inventory(database)

    with pytest.raises(ValueError):
        async with database.session() as session:
            await mark_channel(
                session,
                KNOWN,
                status=ChannelStatus.SEED,
                reject_reason=RejectReason.ADS,
            )


async def test_reviewing_an_unknown_channel_fails(database: Database) -> None:
    await seed_inventory(database)

    with pytest.raises(ChannelNotFoundError):
        async with database.session() as session:
            await mark_channel(session, UNKNOWN, status=ChannelStatus.SEED)


async def test_reviewing_by_username(database: Database) -> None:
    await seed_inventory(database)

    async with database.session() as session:
        channel = await mark_channel(
            session, "example_0", status=ChannelStatus.SEED
        )

    assert channel.tg_id == KNOWN
    assert channel.status is ChannelStatus.SEED


async def test_a_username_matches_case_insensitively(
    database: Database,
) -> None:
    await seed_inventory(database)

    async with database.session() as session:
        channel = await find_channel(session, "Example_0")

    assert channel.tg_id == KNOWN


async def test_an_unknown_username_fails(database: Database) -> None:
    await seed_inventory(database)

    with pytest.raises(ChannelNotFoundError, match="@example_nobody"):
        async with database.session() as session:
            await find_channel(session, "example_nobody")


async def test_a_username_on_two_channels_is_refused(
    database: Database,
) -> None:
    """A rename leaves the old username behind until the next import."""
    await seed_inventory(database)
    async with database.session() as session:
        await upsert_channels(
            session,
            [
                DiscoveredChannel(
                    tg_id=UNKNOWN,
                    username="example_0",
                    title="Example, renamed",
                    is_chat=False,
                )
            ],
            discovered_via=DiscoverySource.MANUAL,
        )

    with pytest.raises(AmbiguousUsernameError):
        async with database.session() as session:
            await mark_channel(
                session, "example_0", status=ChannelStatus.MAYBE
            )

    async with database.session() as session:
        channel = await session.get(Channel, KNOWN)

    assert channel is not None
    assert channel.status is ChannelStatus.CANDIDATE


async def test_a_rejection_can_be_taken_back(database: Database) -> None:
    """The stale reason has to be cleared, or the constraint fires."""
    await seed_inventory(database)

    async with database.session() as session:
        await mark_channel(
            session,
            KNOWN,
            status=ChannelStatus.REJECTED,
            reject_reason=RejectReason.ADJACENT,
            reject_note="mostly design",
        )
    async with database.session() as session:
        channel = await mark_channel(
            session, KNOWN, status=ChannelStatus.SEED, kind=ChannelKind.MEDIA
        )

    assert channel.status is ChannelStatus.SEED
    assert channel.reject_reason is None
    assert channel.reject_note is None


async def test_the_database_refuses_a_reasonless_rejection(
    database: Database,
) -> None:
    with pytest.raises(IntegrityError):
        async with database.session() as session:
            await session.execute(
                text(
                    "insert into channels (tg_id, status, discovered_via) "
                    "values (:tg_id, 'rejected', 'manual')"
                ),
                {"tg_id": UNKNOWN},
            )


async def test_listing_filters_by_status(database: Database) -> None:
    await seed_inventory(database)
    async with database.session() as session:
        await mark_channel(session, KNOWN, status=ChannelStatus.SEED)

    async with database.session() as session:
        seeds = await list_channels(session, status=ChannelStatus.SEED)
        everything = await list_channels(session)

    assert [channel.tg_id for channel in seeds] == [KNOWN]
    assert len(everything) == 3


async def test_rejected_channels_stay_in_the_listing(
    database: Database,
) -> None:
    await seed_inventory(database)
    async with database.session() as session:
        await mark_channel(
            session,
            KNOWN,
            status=ChannelStatus.REJECTED,
            reject_reason=RejectReason.NOT_IT,
        )

    async with database.session() as session:
        everything = await list_channels(session)

    assert KNOWN in {channel.tg_id for channel in everything}


async def test_counts_cover_every_status(database: Database) -> None:
    await seed_inventory(database)
    async with database.session() as session:
        await mark_channel(session, KNOWN, status=ChannelStatus.SEED)

    async with database.session() as session:
        counts = await count_by_status(session)

    assert counts == {
        ChannelStatus.CANDIDATE: 2,
        ChannelStatus.SEED: 1,
        ChannelStatus.MAYBE: 0,
        ChannelStatus.REJECTED: 0,
    }


# --- what an addition by username has to ask ---------------------------


async def test_known_usernames_are_reported_case_insensitively(
    database: Database,
) -> None:
    await seed_inventory(database)
    async with database.session() as session:
        known = await existing_usernames(
            session, ["EXAMPLE_0", "example_1", "not_in_the_inventory"]
        )

    assert known == {"example_0", "example_1"}


async def test_an_empty_list_asks_nothing(database: Database) -> None:
    async with database.session() as session:
        assert await existing_usernames(session, []) == set()


async def test_creating_a_resolved_channel_reports_the_insert(
    database: Database,
) -> None:
    async with database.session() as session:
        inserted = await create_resolved_channel(
            session,
            channel=DiscoveredChannel(
                tg_id=KNOWN,
                username="fake_new",
                title="Fake New",
                is_chat=False,
            ),
            discovered_via=DiscoverySource.MANUAL,
        )

    assert inserted is True


async def test_refreshing_an_existing_channel_reports_no_insert(
    database: Database,
) -> None:
    await seed_inventory(database)
    async with database.session() as session:
        inserted = await create_resolved_channel(
            session,
            channel=DiscoveredChannel(
                tg_id=KNOWN,
                username="example_0",
                title="Renamed",
                is_chat=False,
            ),
            discovered_via=DiscoverySource.MANUAL,
        )

    assert inserted is False


async def test_a_channel_known_only_by_id_is_an_update_not_an_insert(
    database: Database,
) -> None:
    # The case the username query cannot see: a row discovered by forward
    # carries an id and no name, so it is absent from the skip set and
    # only the write knows it was already there.
    async with database.session() as session:
        await upsert_channels(
            session,
            [
                DiscoveredChannel(
                    tg_id=KNOWN, username=None, title=None, is_chat=False
                )
            ],
            discovered_via=DiscoverySource.FORWARD,
        )

    async with database.session() as session:
        assert await existing_usernames(session, ["fake_named"]) == set()
        inserted = await create_resolved_channel(
            session,
            channel=DiscoveredChannel(
                tg_id=KNOWN,
                username="fake_named",
                title="Fake Named",
                is_chat=False,
            ),
            discovered_via=DiscoverySource.MANUAL,
        )

    assert inserted is False


async def test_a_refreshed_channel_keeps_its_provenance(
    database: Database,
) -> None:
    await seed_inventory(database)
    async with database.session() as session:
        await mark_channel(
            session,
            KNOWN,
            status=ChannelStatus.REJECTED,
            reject_reason=RejectReason.NOT_IT,
        )

    async with database.session() as session:
        await create_resolved_channel(
            session,
            channel=DiscoveredChannel(
                tg_id=KNOWN,
                username="example_0",
                title="Example 0",
                is_chat=False,
            ),
            discovered_via=DiscoverySource.MANUAL,
        )

    async with database.session() as session:
        channel = await find_channel(session, KNOWN)

    assert channel.discovered_via is DiscoverySource.OWN_SUBSCRIPTIONS
    assert channel.status is ChannelStatus.REJECTED
