"""Deriving edges from the raw layer — the graph this project exists for.

No network anywhere: derivation reads and writes the database and nothing
else. The client is never even constructed, and one test proves it.
"""

from collections.abc import AsyncIterator
from datetime import datetime
from typing import Any

import pytest
from sqlalchemy import func, select
from sqlalchemy.exc import IntegrityError

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
    EdgeKind,
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
ORIGINAL_DATE = "2026-03-10T08:00:00+00:00"  # the referenced post's own date


# --- payload builders ------------------------------------------------


def raw(
    msg_id: int,
    *,
    fwd: dict[str, Any] | None = None,
    message: str = "",
    entities: list[dict[str, Any]] | None = None,
    date: str = DATE,
    grouped_id: int | None = None,
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
    if grouped_id is not None:
        payload["grouped_id"] = grouped_id
    return payload


def fwd_from_channel(
    channel_id: int,
    *,
    channel_post: int | None = None,
    date: str | None = None,
) -> dict[str, Any]:
    header: dict[str, Any] = {
        "_": "MessageFwdHeader",
        "from_id": {"_": "PeerChannel", "channel_id": channel_id},
    }
    if channel_post is not None:
        header["channel_post"] = channel_post
    if date is not None:
        header["date"] = date
    return header


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


def url_entity(text: str, *, offset: int = 0) -> dict[str, Any]:
    return {"_": "MessageEntityUrl", "offset": offset, "length": len(text)}


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


async def edge_fields(database: Database, msg_id: int) -> list[dict[str, Any]]:
    """Every edge derived from one referencing message, post fields and all.

    Ordered so a message that produces several edges compares stably. The
    dicts are built inside the session so nothing is read after it closes.
    """
    async with database.session() as session:
        rows = await session.scalars(
            select(Edge)
            .where(Edge.msg_id == msg_id)
            .order_by(Edge.kind, Edge.dst_channel_id, Edge.dst_msg_id)
        )
        return [
            {
                "src": e.src_channel_id,
                "dst": e.dst_channel_id,
                "kind": e.kind.value,
                "dst_msg_id": e.dst_msg_id,
                "dst_published_at": e.dst_published_at,
                "grouped_id": e.grouped_id,
            }
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


# --- post-level references -------------------------------------------


async def test_a_forward_carries_the_referenced_post_and_its_date(
    inventory: Database,
) -> None:
    async with inventory.session() as session:
        await seed(
            session,
            raw(
                80,
                fwd=fwd_from_channel(
                    KNOWN, channel_post=555, date=ORIGINAL_DATE
                ),
            ),
        )

    await derive_graph(inventory)

    assert await edge_fields(inventory, 80) == [
        {
            "src": SRC,
            "dst": KNOWN,
            "kind": "forward",
            "dst_msg_id": 555,
            "dst_published_at": datetime.fromisoformat(ORIGINAL_DATE),
            "grouped_id": None,
        }
    ]


async def test_a_forward_naming_no_original_post_still_becomes_an_edge(
    inventory: Database,
) -> None:
    # Spec: the edge is recorded with its referenced-message fields empty,
    # and not discarded.
    async with inventory.session() as session:
        await seed(session, raw(81, fwd=fwd_from_channel(KNOWN)))

    summary = await derive_graph(inventory)

    assert await edge_fields(inventory, 81) == [
        {
            "src": SRC,
            "dst": KNOWN,
            "kind": "forward",
            "dst_msg_id": None,
            "dst_published_at": None,
            "grouped_id": None,
        }
    ]
    assert summary.edges == 1


async def test_a_forwarded_album_is_one_edge_per_message_sharing_a_group(
    inventory: Database,
) -> None:
    # Spec: each message of a forwarded album produces its own edge, every
    # one carrying the same group id; derivation does not merge them.
    group = 7788990011
    async with inventory.session() as session:
        await seed(
            session,
            *(
                raw(
                    90 + offset,
                    fwd=fwd_from_channel(
                        KNOWN, channel_post=offset, date=ORIGINAL_DATE
                    ),
                    grouped_id=group,
                )
                for offset in (1, 2, 3)
            ),
        )

    summary = await derive_graph(inventory)

    assert summary.edges == 3  # one per message, not one merged edge
    for msg_id in (91, 92, 93):
        fields = await edge_fields(inventory, msg_id)
        assert len(fields) == 1
        assert fields[0]["grouped_id"] == group


async def test_a_post_link_carries_the_referenced_post_id(
    inventory: Database,
) -> None:
    # Spec: a t.me link to one message records a mention edge carrying that
    # message's id. A link carries no original date — only a forward does.
    link = "t.me/known_dst/77"
    async with inventory.session() as session:
        await seed(
            session, raw(100, message=link, entities=[url_entity(link)])
        )

    await derive_graph(inventory)

    assert await edge_fields(inventory, 100) == [
        {
            "src": SRC,
            "dst": KNOWN,
            "kind": "mention",
            "dst_msg_id": 77,
            "dst_published_at": None,
            "grouped_id": None,
        }
    ]


async def test_two_links_to_different_posts_of_one_channel_are_two_edges(
    inventory: Database,
) -> None:
    # Spec: an edge for each referenced post.
    text = "t.me/known_dst/10 t.me/known_dst/20"
    async with inventory.session() as session:
        await seed(
            session,
            raw(
                101,
                message=text,
                entities=[
                    url_entity("t.me/known_dst/10", offset=0),
                    url_entity("t.me/known_dst/20", offset=18),
                ],
            ),
        )

    await derive_graph(inventory)

    fields = await edge_fields(inventory, 101)
    assert {f["dst_msg_id"] for f in fields} == {10, 20}
    assert all(f["dst"] == KNOWN and f["kind"] == "mention" for f in fields)


async def test_a_channel_by_name_and_by_post_link_is_two_edges(
    inventory: Database,
) -> None:
    # Spec: two edges — one naming no post, one naming that post.
    text = "@known_dst t.me/known_dst/9"
    async with inventory.session() as session:
        await seed(
            session,
            raw(
                102,
                message=text,
                entities=[
                    mention_entity("known_dst", offset=0),
                    url_entity("t.me/known_dst/9", offset=11),
                ],
            ),
        )

    await derive_graph(inventory)

    fields = await edge_fields(inventory, 102)
    assert {f["dst_msg_id"] for f in fields} == {None, 9}
    assert all(f["dst"] == KNOWN and f["kind"] == "mention" for f in fields)


async def test_the_same_post_referenced_twice_is_one_edge(
    inventory: Database,
) -> None:
    # Spec: one edge for a post referenced more than once in one message.
    text = "t.me/known_dst/5 t.me/known_dst/5"
    async with inventory.session() as session:
        await seed(
            session,
            raw(
                103,
                message=text,
                entities=[
                    url_entity("t.me/known_dst/5", offset=0),
                    url_entity("t.me/known_dst/5", offset=17),
                ],
            ),
        )

    await derive_graph(inventory)

    fields = await edge_fields(inventory, 103)
    assert len(fields) == 1
    assert fields[0]["dst_msg_id"] == 5


async def test_two_identical_mention_edges_conflict_at_the_database(
    inventory: Database,
) -> None:
    """The ``NULLS NOT DISTINCT`` constraint, proven directly.

    A mention edge has a null ``dst_msg_id``. Under Postgres's default
    (nulls distinct) two of them would both insert, and every re-run of
    derivation would duplicate the edge — silently, since ``ON CONFLICT DO
    NOTHING`` never fires on a conflict Postgres does not see. Inserting
    the second one raw, past the conflict handler, must raise. This is the
    failure the change is most likely to reintroduce and it is invisible
    in application code.
    """

    def mention_edge() -> Edge:
        return Edge(
            src_channel_id=SRC,
            dst_channel_id=KNOWN,
            kind=EdgeKind.MENTION,
            msg_id=200,
            published_at=datetime.fromisoformat(DATE),
            dst_msg_id=None,
        )

    async with inventory.session() as session:
        session.add(mention_edge())

    with pytest.raises(IntegrityError):
        async with inventory.session() as session:
            session.add(mention_edge())


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
