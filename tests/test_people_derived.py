"""Nothing derived depends on a person.

The same posts are derived from twice — as Telegram sent them and with what
names a person removed — and everything derived from them must agree:
references, readings, YouTube links, and the edges and pending mentions the
derivation pass writes. Alerts are not compared on their own: they are worked
out from edges and readings, and those are.
"""

import json
from collections.abc import AsyncIterator
from typing import Any

import pytest
from people_cases import (
    ALL,
    OTHER_USER,
    Case,
    fields_naming_people,
    peer_channel,
    post,
)
from sqlalchemy import select, text

from itgraph.db.channels import (
    DiscoveredChannel,
    mark_channel,
    upsert_channels,
)
from itgraph.db.models import (
    ChannelKind,
    ChannelStatus,
    DiscoverySource,
    Edge,
    PendingMentionSource,
)
from itgraph.db.raw import store_messages
from itgraph.db.session import Database
from itgraph.derive.edges import derive_graph
from itgraph.derive.metrics import counters_of
from itgraph.derive.references import extract_references
from itgraph.derive.tg_youtube import is_own, read_post
from itgraph.tg.payload import without_people

SRC = 2000000001
KNOWN = 2000000002
UNKNOWN_HANDLE = "fake_unknown_dst"


def mention(name: str, offset: int) -> dict[str, Any]:
    return {
        "_": "MessageEntityMention",
        "offset": offset,
        "length": len(name) + 1,
    }


def url(link: str, offset: int) -> dict[str, Any]:
    return {"_": "MessageEntityUrl", "offset": offset, "length": len(link)}


def posts() -> list[dict[str, Any]]:
    """Posts that name people and reference channels and YouTube at once."""
    people = fields_naming_people()
    tme = "https://t.me/known_dst/7"
    youtube = "https://youtu.be/fakeVideo01"
    text_mentions = f"see @known_dst and @{UNKNOWN_HANDLE}"
    built = [
        # A forward from a known channel, carrying every person at once.
        post(
            id=1,
            **{
                **people,
                "fwd_from": {
                    **people["fwd_from"],
                    "from_id": peer_channel(KNOWN),
                    "channel_post": 5,
                },
            },
        ),
        # A forward from a user: no edge either way.
        post(id=2, **people),
        post(
            id=3,
            message=text_mentions,
            entities=[
                *people["entities"][:2],
                mention("known_dst", text_mentions.index("@known_dst")),
                mention(
                    UNKNOWN_HANDLE, text_mentions.index("@" + UNKNOWN_HANDLE)
                ),
            ],
            replies=people["replies"],
        ),
        post(
            id=4,
            message=f"{tme} {youtube}",
            entities=[url(tme, 0), url(youtube, len(tme) + 1)],
            reactions=people["reactions"],
            post_author="A Fake Author",
        ),
    ]
    for offset, case in enumerate(ALL, start=100):
        if case.received.get("_") == "Message":
            built.append({**case.received, "id": offset})
    return built


@pytest.mark.parametrize("case", ALL, ids=[case.name for case in ALL])
def test_the_pure_readings_agree(case: Case) -> None:
    received, cleared = case.received, without_people(case.received)

    assert extract_references(received) == extract_references(cleared)
    assert counters_of(received) == counters_of(cleared)
    assert read_post(received) == read_post(cleared)
    assert is_own(received) == is_own(cleared)


def test_the_built_posts_agree_too() -> None:
    for payload in posts():
        cleared = without_people(payload)
        assert cleared != payload or payload["id"] >= 100
        assert extract_references(payload) == extract_references(cleared)
        assert counters_of(payload) == counters_of(cleared)
        assert read_post(payload) == read_post(cleared)
        assert is_own(payload) == is_own(cleared)


@pytest.fixture
async def inventory(database: Database) -> AsyncIterator[Database]:
    async with database.session() as session:
        await upsert_channels(
            session,
            [
                DiscoveredChannel(SRC, "fake_src_channel", "Source", False),
                DiscoveredChannel(KNOWN, "known_dst", "Known", False),
            ],
            discovered_via=DiscoverySource.MANUAL,
        )
        for tg_id in (SRC, KNOWN):
            await mark_channel(
                session,
                tg_id,
                status=ChannelStatus.SEED,
                kind=ChannelKind.PERSONAL,
            )
    yield database


async def derived(database: Database) -> tuple[list[Any], list[Any]]:
    async with database.session() as session:
        edges = [
            (
                e.src_channel_id,
                e.msg_id,
                e.kind.value,
                e.dst_channel_id,
                e.dst_msg_id,
                e.dst_published_at,
            )
            for e in await session.scalars(
                select(Edge).order_by(
                    Edge.msg_id,
                    Edge.kind,
                    Edge.dst_channel_id,
                    Edge.dst_msg_id,
                )
            )
        ]
        pending = [
            (s.username, s.channel_id)
            for s in await session.scalars(
                select(PendingMentionSource).order_by(
                    PendingMentionSource.username,
                    PendingMentionSource.channel_id,
                )
            )
        ]
    return edges, pending


async def test_the_derivation_pass_writes_the_same(
    inventory: Database,
) -> None:
    built = posts()
    async with inventory.session() as session:
        await store_messages(
            session, channel_id=SRC, payloads={p["id"]: p for p in built}
        )
        await session.commit()

    await derive_graph(inventory)
    as_received = await derived(inventory)

    async with inventory.session() as session:
        for payload in built:
            await session.execute(
                text(
                    "UPDATE raw_messages SET payload = CAST(:p AS jsonb) "
                    "WHERE channel_id = :c AND msg_id = :m"
                ),
                {
                    "p": json.dumps(without_people(payload)),
                    "c": SRC,
                    "m": payload["id"],
                },
            )
        await session.commit()
        stored = await session.scalar(
            text(
                "SELECT count(*) FROM raw_messages "
                "WHERE payload::text LIKE :user"
            ),
            {"user": f"%{OTHER_USER}%"},
        )
    assert stored == 0

    await derive_graph(inventory, rebuild=True)
    as_cleared = await derived(inventory)

    edges, pending = as_received
    # Proof the posts derive to something: an empty comparison proves nothing.
    assert {kind for _, _, kind, *_ in edges} >= {"forward", "mention"}
    assert {username for username, *_ in pending} == {
        "fake_handle",
        UNKNOWN_HANDLE,
    }
    assert as_cleared == as_received
