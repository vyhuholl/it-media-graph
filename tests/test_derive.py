"""Deriving edges from the raw layer — the graph this project exists for.

No network anywhere: derivation reads and writes the database and nothing
else. The client is never even constructed, and one test proves it.
"""

from collections.abc import AsyncIterator
from typing import Any

import pytest
from sqlalchemy import func, select

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
    Edge,
    PendingMention,
    RawMessage,
)
from itgraph.db.raw import store_messages
from itgraph.db.session import Database
from itgraph.derive.edges import derive_graph

SRC = 1000000001
KNOWN = 1000000002
FWD_ONLY = 2000000001  # not in the inventory until a forward discovers it

DATE = "2026-03-14T09:26:53+00:00"


# --- payload builders ------------------------------------------------


def raw(
    msg_id: int,
    *,
    fwd: dict[str, Any] | None = None,
    message: str = "",
    entities: list[dict[str, Any]] | None = None,
    date: str = DATE,
) -> dict[str, Any]:
    payload: dict[str, Any] = {
        "_": "Message",
        "id": msg_id,
        "date": date,
        "message": message,
        "entities": entities or [],
    }
    if fwd is not None:
        payload["fwd_from"] = fwd
    return payload


def fwd_from_channel(channel_id: int) -> dict[str, Any]:
    return {
        "_": "MessageFwdHeader",
        "from_id": {"_": "PeerChannel", "channel_id": channel_id},
    }


def fwd_from_user(user_id: int) -> dict[str, Any]:
    return {
        "_": "MessageFwdHeader",
        "from_id": {"_": "PeerUser", "user_id": user_id},
    }


def fwd_hidden() -> dict[str, Any]:
    return {"_": "MessageFwdHeader", "from_id": None, "from_name": "Someone"}


def mention_entity(name: str, *, offset: int = 0) -> dict[str, Any]:
    # An @mention entity spans "@name"; ASCII, so units == characters.
    return {
        "_": "MessageEntityMention",
        "offset": offset,
        "length": len(name) + 1,
    }


def url_entity(text: str) -> dict[str, Any]:
    return {"_": "MessageEntityUrl", "offset": 0, "length": len(text)}


# --- fixtures --------------------------------------------------------


async def seed(session: Any, *messages: dict[str, Any]) -> None:
    """Store a batch of raw messages under the source channel."""
    await store_messages(
        session,
        channel_id=SRC,
        payloads={payload["id"]: payload for payload in messages},
    )


@pytest.fixture
async def inventory(database: Database) -> AsyncIterator[Database]:
    """One seed source channel and one known target, nothing else."""
    async with database.session() as session:
        await upsert_channels(
            session,
            [
                DiscoveredChannel(SRC, "src_channel", "Source", False),
                DiscoveredChannel(KNOWN, "known_dst", "Known", False),
            ],
            discovered_via=DiscoverySource.OWN_SUBSCRIPTIONS,
        )
        for tg_id in (SRC, KNOWN):
            await mark_channel(
                session,
                tg_id,
                status=ChannelStatus.SEED,
                kind=ChannelKind.PERSONAL,
            )
    yield database


# --- helpers ---------------------------------------------------------


async def edges_of(database: Database) -> list[tuple[int, int, str, int]]:
    async with database.session() as session:
        rows = await session.scalars(
            select(Edge).order_by(Edge.msg_id, Edge.kind, Edge.dst_channel_id)
        )
        return [
            (e.src_channel_id, e.dst_channel_id, e.kind.value, e.msg_id)
            for e in rows
        ]


async def pending_of(database: Database) -> set[str]:
    async with database.session() as session:
        return set(
            (await session.scalars(select(PendingMention.username))).all()
        )


# --- forwards --------------------------------------------------------


async def test_a_forward_from_a_channel_becomes_an_edge(
    inventory: Database,
) -> None:
    async with inventory.session() as session:
        await seed(session, raw(10, fwd=fwd_from_channel(FWD_ONLY)))

    summary = await derive_graph(inventory)

    assert (SRC, FWD_ONLY, "forward", 10) in await edges_of(inventory)
    assert summary.edges == 1
    # The far endpoint was discovered and created as an unreviewed
    # candidate marked by where it came from.
    async with inventory.session() as session:
        discovered = await session.get(Channel, FWD_ONLY)
    assert discovered is not None
    assert discovered.status is ChannelStatus.CANDIDATE
    assert discovered.discovered_via is DiscoverySource.FORWARD
    assert discovered.username is None
    assert discovered.resolved_at is None
    assert summary.discovered == 1


async def test_a_forward_from_a_user_is_no_edge(inventory: Database) -> None:
    async with inventory.session() as session:
        await seed(session, raw(11, fwd=fwd_from_user(555)))

    summary = await derive_graph(inventory)

    assert await edges_of(inventory) == []
    assert summary.edges == 0


async def test_a_forward_with_a_hidden_origin_is_no_edge(
    inventory: Database,
) -> None:
    async with inventory.session() as session:
        await seed(session, raw(12, fwd=fwd_hidden()))

    await derive_graph(inventory)

    assert await edges_of(inventory) == []


async def test_a_self_forward_is_no_edge(inventory: Database) -> None:
    async with inventory.session() as session:
        await seed(session, raw(13, fwd=fwd_from_channel(SRC)))

    await derive_graph(inventory)

    assert await edges_of(inventory) == []


# --- mentions --------------------------------------------------------


async def test_a_mention_of_a_known_channel_becomes_an_edge(
    inventory: Database,
) -> None:
    async with inventory.session() as session:
        await seed(
            session,
            raw(
                20,
                message="@known_dst",
                entities=[mention_entity("known_dst")],
            ),
        )

    summary = await derive_graph(inventory)

    assert (SRC, KNOWN, "mention", 20) in await edges_of(inventory)
    assert summary.pending == 0


async def test_a_mention_of_an_unknown_channel_waits_pending(
    inventory: Database,
) -> None:
    async with inventory.session() as session:
        await seed(
            session,
            raw(
                21,
                message="@newcomer",
                entities=[mention_entity("newcomer")],
            ),
        )

    summary = await derive_graph(inventory)

    # No edge yet — it cannot be written without an id.
    assert await edges_of(inventory) == []
    assert await pending_of(inventory) == {"newcomer"}
    assert summary.pending == 1


async def test_a_tme_link_to_a_known_channel_becomes_a_mention_edge(
    inventory: Database,
) -> None:
    link = "t.me/known_dst"
    async with inventory.session() as session:
        await seed(session, raw(22, message=link, entities=[url_entity(link)]))

    await derive_graph(inventory)

    assert (SRC, KNOWN, "mention", 22) in await edges_of(inventory)


async def test_an_id_link_to_a_known_channel_becomes_a_mention_edge(
    inventory: Database,
) -> None:
    link = f"t.me/c/{KNOWN}/9"
    async with inventory.session() as session:
        await seed(session, raw(23, message=link, entities=[url_entity(link)]))

    await derive_graph(inventory)

    assert (SRC, KNOWN, "mention", 23) in await edges_of(inventory)


async def test_an_id_link_to_an_unknown_channel_is_dropped(
    inventory: Database,
) -> None:
    link = f"t.me/c/{FWD_ONLY}/9"
    async with inventory.session() as session:
        await seed(session, raw(24, message=link, entities=[url_entity(link)]))

    await derive_graph(inventory)

    # A bare id from a link is not trusted enough to create a channel for.
    assert await edges_of(inventory) == []
    async with inventory.session() as session:
        assert await session.get(Channel, FWD_ONLY) is None


async def test_a_repeated_reference_in_one_message_is_one_edge(
    inventory: Database,
) -> None:
    async with inventory.session() as session:
        await seed(
            session,
            raw(
                25,
                message="@known_dst and again @known_dst",
                entities=[
                    mention_entity("known_dst", offset=0),
                    mention_entity("known_dst", offset=21),
                ],
            ),
        )

    await derive_graph(inventory)

    edges = [e for e in await edges_of(inventory) if e[3] == 25]
    assert edges == [(SRC, KNOWN, "mention", 25)]


async def test_a_forward_and_a_mention_of_it_are_two_edges(
    inventory: Database,
) -> None:
    """Different kinds of the same relationship are both real."""
    async with inventory.session() as session:
        await seed(
            session,
            raw(
                26,
                fwd=fwd_from_channel(KNOWN),
                message="@known_dst",
                entities=[mention_entity("known_dst")],
            ),
        )

    await derive_graph(inventory)

    edges = {e for e in await edges_of(inventory) if e[3] == 26}
    assert edges == {
        (SRC, KNOWN, "forward", 26),
        (SRC, KNOWN, "mention", 26),
    }


# --- repeatability and invariants ------------------------------------


async def test_two_runs_produce_identical_edges_and_the_second_writes_nothing(
    inventory: Database,
) -> None:
    async with inventory.session() as session:
        await seed(
            session,
            raw(30, fwd=fwd_from_channel(FWD_ONLY)),
            raw(
                31,
                message="@known_dst",
                entities=[mention_entity("known_dst")],
            ),
        )

    first = await derive_graph(inventory)
    edges_after_first = await edges_of(inventory)

    second = await derive_graph(inventory)
    edges_after_second = await edges_of(inventory)

    assert edges_after_first == edges_after_second
    assert first.edges == 2
    # The second pass sees the same raw data and the endpoint already
    # exists, so it writes neither an edge nor a channel.
    assert second.edges == 0
    assert second.discovered == 0


async def test_rebuild_truncates_then_recreates_without_touching_channels(
    inventory: Database,
) -> None:
    async with inventory.session() as session:
        await seed(session, raw(40, fwd=fwd_from_channel(FWD_ONLY)))

    await derive_graph(inventory)
    before = await edges_of(inventory)

    summary = await derive_graph(inventory, rebuild=True)

    # The discovered channel survives the rebuild, so the edge to it is
    # re-derived rather than lost.
    assert await edges_of(inventory) == before
    assert summary.edges == 1
    assert summary.discovered == 0  # already exists from the first run
    async with inventory.session() as session:
        assert await session.get(Channel, FWD_ONLY) is not None
        raw_still_there = await session.scalar(
            select(func.count()).select_from(RawMessage)
        )
    assert raw_still_there == 1


async def test_every_edge_endpoint_exists_in_the_inventory(
    inventory: Database,
) -> None:
    async with inventory.session() as session:
        await seed(
            session,
            raw(50, fwd=fwd_from_channel(FWD_ONLY)),
            raw(
                51,
                message="@known_dst",
                entities=[mention_entity("known_dst")],
            ),
        )

    await derive_graph(inventory)

    async with inventory.session() as session:
        orphans = await session.scalar(
            select(func.count())
            .select_from(Edge)
            .outerjoin(Channel, Channel.tg_id == Edge.dst_channel_id)
            .where(Channel.tg_id.is_(None))
        )
    assert orphans == 0


async def test_derivation_makes_no_telegram_request(
    inventory: Database, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Constructing a client at all fails the test.

    Derivation must depend only on stored data. Wiring the client
    constructor to explode is the strongest way to prove it never reaches
    for the network.
    """
    import itgraph.tg.client as tg_client

    def explode(*_: Any, **__: Any) -> None:
        raise AssertionError("derivation constructed a Telegram client")

    monkeypatch.setattr(tg_client, "build_client", explode)

    async with inventory.session() as session:
        await seed(session, raw(60, fwd=fwd_from_channel(FWD_ONLY)))

    summary = await derive_graph(inventory)

    assert summary.edges == 1


async def test_no_user_id_is_ever_written_to_a_derived_table(
    inventory: Database,
) -> None:
    """Forwards from users and hidden origins leave no trace downstream."""
    async with inventory.session() as session:
        await seed(
            session,
            raw(70, fwd=fwd_from_user(424242)),
            raw(71, fwd=fwd_hidden()),
        )

    await derive_graph(inventory)

    async with inventory.session() as session:
        edge_count = await session.scalar(
            select(func.count()).select_from(Edge)
        )
        pending_count = await session.scalar(
            select(func.count()).select_from(PendingMention)
        )
    assert edge_count == 0
    assert pending_count == 0
