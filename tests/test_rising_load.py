"""What the rising ranking reads, and the journal of resolutions.

Against a ``_test`` database; ids and names are fake and spelled so.
"""

from datetime import UTC, datetime, timedelta

from sqlalchemy import text

from itgraph.db.channels import (
    NOT_A_CHANNEL,
    DiscoveredChannel,
    confirm_affiliation,
    mark_channel,
    record_channel_resolve_failure,
    upsert_channels,
)
from itgraph.db.edges import (
    EdgeRow,
    MentionSource,
    add_pending_mentions,
    create_discovered_channels,
    insert_edges,
    record_pending_failure,
)
from itgraph.db.models import (
    ChannelKind,
    ChannelStatus,
    CollectionCommand,
    DiscoverySource,
    EdgeKind,
    RejectReason,
    ResolutionKind,
    ResolutionOutcome,
)
from itgraph.db.rising import (
    load_rising_rows,
    record_resolution,
    resolutions_since,
)
from itgraph.db.session import Database

SEED_A = 1000000001
SEED_B = 1000000002
CHAT_SEED = 1000000003
CANDIDATE = 2000000001
FORWARDED = 2000000002  # by id, never resolved
REJECTED = 2000000003
AT = datetime(2026, 11, 10, 9, tzinfo=UTC)


async def inventory(database: Database) -> None:
    """Two seeds, a chat seed, a candidate, a forwarded id, a rejected one."""
    async with database.session() as session:
        await upsert_channels(
            session,
            [
                DiscoveredChannel(SEED_A, "fake_seed_a", "Seed A", False),
                DiscoveredChannel(SEED_B, "fake_seed_b", "Seed B", False),
                DiscoveredChannel(CHAT_SEED, "fake_chat", "Chat", True),
                DiscoveredChannel(CANDIDATE, "fake_cand", "Candidate", False),
                DiscoveredChannel(REJECTED, "fake_rejected", "Out", False),
            ],
            discovered_via=DiscoverySource.OWN_SUBSCRIPTIONS,
        )
        for seed in (SEED_A, SEED_B, CHAT_SEED):
            await mark_channel(
                session,
                seed,
                status=ChannelStatus.SEED,
                kind=ChannelKind.PERSONAL,
            )
        await mark_channel(
            session,
            REJECTED,
            status=ChannelStatus.REJECTED,
            reject_reason=RejectReason.NOT_IT,
        )
        await create_discovered_channels(
            session, tg_ids=[FORWARDED], discovered_via=DiscoverySource.FORWARD
        )
        await session.commit()


def edge(src: int, dst: int, msg_id: int) -> EdgeRow:
    return EdgeRow(
        src_channel_id=src,
        dst_channel_id=dst,
        kind=EdgeKind.MENTION,
        msg_id=msg_id,
        published_at=AT,
    )


async def test_only_seeds_edges_to_candidates_are_read(
    database: Database,
) -> None:
    await inventory(database)
    async with database.session() as session:
        await insert_edges(
            session,
            [
                edge(SEED_A, CANDIDATE, 1),
                edge(SEED_B, FORWARDED, 2),
                # A candidate's edge, a chat seed's, and one to a rejected
                # channel: none of them is read.
                edge(FORWARDED, CANDIDATE, 3),
                edge(CHAT_SEED, CANDIDATE, 4),
                edge(SEED_A, REJECTED, 5),
            ],
        )
        await session.commit()

    async with database.session() as session:
        rows = await load_rising_rows(session)

    assert rows.seeds == frozenset({SEED_A, SEED_B})
    assert sorted((ref.src, ref.dst, ref.at) for ref in rows.references) == [
        (SEED_A, CANDIDATE, AT),
        (SEED_B, FORWARDED, AT),
    ]
    assert set(rows.channels) == {CANDIDATE, FORWARDED}
    assert rows.channels[CANDIDATE].username == "fake_cand"
    assert rows.channels[CANDIDATE].resolved
    assert not rows.channels[FORWARDED].resolved
    assert rows.channels[FORWARDED].attempts == 0


async def test_a_channel_s_failure_is_read_as_its_kind(
    database: Database,
) -> None:
    await inventory(database)
    async with database.session() as session:
        await insert_edges(session, [edge(SEED_A, FORWARDED, 1)])
        await record_channel_resolve_failure(session, FORWARDED, NOT_A_CHANNEL)
        await session.commit()

    async with database.session() as session:
        rows = await load_rising_rows(session)

    assert rows.channels[FORWARDED].attempts == 1
    assert rows.channels[FORWARDED].not_channel


async def test_a_confirmed_family_is_read(database: Database) -> None:
    await inventory(database)
    async with database.session() as session:
        await confirm_affiliation(session, [SEED_A, SEED_B])
        await session.commit()

    async with database.session() as session:
        rows = await load_rising_rows(session)

    assert rows.families[SEED_A] == rows.families[SEED_B]


async def test_pending_sources_and_their_names_are_read(
    database: Database,
) -> None:
    await inventory(database)
    async with database.session() as session:
        await add_pending_mentions(
            session,
            [
                MentionSource(SEED_A, "fake_name", AT),
                MentionSource(SEED_A, "fake_name", AT + timedelta(days=2)),
                MentionSource(CHAT_SEED, "fake_name", AT),
                MentionSource(SEED_B, "fake_person", AT),
                # A name a channel already answers to, spelled otherwise.
                MentionSource(SEED_B, "fake_cand", AT),
                # Mentioned by a non-seed only.
                MentionSource(CANDIDATE, "fake_elsewhere", AT),
            ],
        )
        await record_pending_failure(session, "fake_person", NOT_A_CHANNEL)
        await session.commit()

    async with database.session() as session:
        rows = await load_rising_rows(session)

    assert sorted(
        (source.username, source.src, source.first, source.last)
        for source in rows.sources
    ) == [
        ("fake_cand", SEED_B, AT, AT),
        ("fake_name", SEED_A, AT, AT + timedelta(days=2)),
        ("fake_person", SEED_B, AT, AT),
    ]
    assert set(rows.names) == {"fake_name", "fake_person"}
    assert rows.names["fake_name"].attempts == 0
    assert rows.names["fake_person"].not_channel


async def test_nothing_is_stored(database: Database) -> None:
    await inventory(database)
    async with database.session() as session:
        await insert_edges(session, [edge(SEED_A, CANDIDATE, 1)])
        await add_pending_mentions(
            session, [MentionSource(SEED_A, "fake_name", AT)]
        )
        await session.commit()

    async with database.session() as session:
        await load_rising_rows(session)
        # A transaction that wrote nothing was never given an id.
        assigned = await session.scalar(
            text("SELECT txid_current_if_assigned()")
        )

    assert assigned is None


# --- the journal --------------------------------------------------------


async def test_the_journal_counts_by_command_since_a_moment(
    database: Database,
) -> None:
    async with database.session() as session:
        for command in (
            CollectionCommand.RESOLVE,
            CollectionCommand.RESOLVE,
            CollectionCommand.WATCH,
        ):
            await record_resolution(
                session,
                command,
                ResolutionKind.USERNAME,
                ResolutionOutcome.CHANNEL,
            )
        await session.commit()

    async with database.session() as session:
        before = await resolutions_since(
            session, datetime.now(UTC) - timedelta(hours=24)
        )
        after = await resolutions_since(
            session, datetime.now(UTC) + timedelta(minutes=1)
        )

    assert before == {CollectionCommand.RESOLVE: 2, CollectionCommand.WATCH: 1}
    assert after == {}
