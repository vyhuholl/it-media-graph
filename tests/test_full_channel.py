"""The per-channel metadata pass, and the discussion chat it resolves."""

import base64
from collections.abc import AsyncIterator

import pytest
from fakes import FakeChannel, FakeFullChannel, FakeTelegramClient
from sqlalchemy import select

from itgraph.db.channels import DiscoveredChannel, upsert_channels
from itgraph.db.models import Channel, DiscoverySource, RawChannel
from itgraph.db.session import Database
from itgraph.tg.full_channel import fetch_full_channel

PARENT = FakeChannel(1000000001, "example_notes", "Example Notes")
CHAT = FakeChannel(1000000002, "example_notes_chat", "Example Notes - chat")
LONELY = FakeChannel(1000000005, "example_jobs", "Example Jobs")


@pytest.fixture
async def inventory(database: Database) -> AsyncIterator[Database]:
    """The channels the pass will run over, already reviewed.

    `raw_channels` has a foreign key onto `channels`, so a payload
    cannot arrive for a channel the inventory has never heard of. The
    walker only ever selects in-scope channels, so this is the state it
    always finds.
    """
    async with database.session() as session:
        await upsert_channels(
            session,
            [
                DiscoveredChannel(
                    tg_id=channel.id,
                    username=getattr(channel, "username", None),
                    title=channel.title,
                    is_chat=False,
                )
                for channel in (PARENT, LONELY)
            ],
            discovered_via=DiscoverySource.OWN_SUBSCRIPTIONS,
        )
    yield database


def client_with_chat() -> FakeTelegramClient:
    return FakeTelegramClient(
        entities={"example_notes": PARENT},
        full_channels={
            PARENT.id: FakeFullChannel(
                PARENT, linked_chat=CHAT, about="notes about things"
            )
        },
    )


async def test_the_payload_is_stored_as_it_arrives(
    inventory: Database,
) -> None:
    client = client_with_chat()

    async with inventory.session() as session:
        await fetch_full_channel(client, session, username="example_notes")

    async with inventory.session() as session:
        row = await session.get(RawChannel, PARENT.id)

    assert row is not None
    assert row.payload["_"] == "messages.ChatFull"
    assert row.payload["full_chat"]["about"] == "notes about things"
    # Went through the encoder, so neither type survived as itself.
    photo = row.payload["full_chat"]["chat_photo"]
    assert photo["date"] == "2026-01-09T07:00:00+00:00"
    assert base64.b64decode(photo["file_reference"]) == b"\x00\xc3reference"


async def test_a_linked_chat_enters_the_inventory(
    inventory: Database,
) -> None:
    client = client_with_chat()

    async with inventory.session() as session:
        result = await fetch_full_channel(
            client, session, username="example_notes"
        )

    assert result.linked_chat_id == CHAT.id

    async with inventory.session() as session:
        chat = await session.get(Channel, CHAT.id)

    assert chat is not None
    assert chat.discovered_via is DiscoverySource.LINKED_CHAT
    assert chat.linked_to == PARENT.id
    assert chat.is_chat is True
    assert chat.username == "example_notes_chat"
    # The chat is not reviewed independently, so it carries no decision.
    assert chat.reviewed_at is None


async def test_an_already_known_chat_is_linked_not_duplicated(
    inventory: Database,
) -> None:
    """A chat imported from the operator's own subscriptions.

    Its discovery source and first-seen timestamp are a record of how it
    was found, and the metadata pass is not a second discovery.
    """
    async with inventory.session() as session:
        await upsert_channels(
            session,
            [
                DiscoveredChannel(
                    tg_id=CHAT.id,
                    username="example_notes_chat",
                    title="Example Notes - chat",
                    is_chat=True,
                )
            ],
            discovered_via=DiscoverySource.OWN_SUBSCRIPTIONS,
        )
        original = await session.get(Channel, CHAT.id)
        assert original is not None
        first_seen_at = original.first_seen_at

    async with inventory.session() as session:
        await fetch_full_channel(
            client_with_chat(), session, username="example_notes"
        )

    async with inventory.session() as session:
        rows = (
            await session.scalars(
                select(Channel).where(Channel.tg_id == CHAT.id)
            )
        ).all()

    assert len(rows) == 1
    chat = rows[0]
    assert chat.linked_to == PARENT.id
    assert chat.discovered_via is DiscoverySource.OWN_SUBSCRIPTIONS
    assert chat.first_seen_at == first_seen_at


async def test_a_channel_without_a_discussion_chat(
    inventory: Database,
) -> None:
    client = FakeTelegramClient(
        entities={"example_jobs": LONELY},
        full_channels={LONELY.id: FakeFullChannel(LONELY)},
    )

    async with inventory.session() as session:
        result = await fetch_full_channel(
            client, session, username="example_jobs"
        )

    assert result.linked_chat_id is None

    async with inventory.session() as session:
        linked = (
            await session.scalars(
                select(Channel).where(Channel.linked_to.is_not(None))
            )
        ).all()

    assert linked == []


async def test_a_second_pass_refreshes_rather_than_accumulates(
    inventory: Database,
) -> None:
    """The freshest payload wins: a description is current or it is noise."""
    async with inventory.session() as session:
        await fetch_full_channel(
            client_with_chat(), session, username="example_notes"
        )

    later = FakeTelegramClient(
        entities={"example_notes": PARENT},
        full_channels={
            PARENT.id: FakeFullChannel(
                PARENT, linked_chat=CHAT, about="notes, now about other things"
            )
        },
    )
    async with inventory.session() as session:
        await fetch_full_channel(later, session, username="example_notes")

    async with inventory.session() as session:
        rows = (await session.scalars(select(RawChannel))).all()

    assert len(rows) == 1
    assert rows[0].payload["full_chat"]["about"] == (
        "notes, now about other things"
    )


async def test_the_entity_is_handed_back_for_the_history_walk(
    inventory: Database,
) -> None:
    """Resolution is a request, and every request on this path is counted."""
    client = client_with_chat()

    async with inventory.session() as session:
        result = await fetch_full_channel(
            client, session, username="example_notes"
        )

    assert result.entity is PARENT
    assert result.tg_id == PARENT.id
    assert client.resolved == ["example_notes"]


async def test_a_missing_chat_in_the_response_does_not_lose_the_payload(
    inventory: Database,
) -> None:
    """The link is a convenience; the payload is the thing worth keeping."""
    orphaned = FakeFullChannel(PARENT, linked_chat=CHAT)
    orphaned.chats = [PARENT]  # the chat Telegram named is not here

    client = FakeTelegramClient(
        entities={"example_notes": PARENT},
        full_channels={PARENT.id: orphaned},
    )

    async with inventory.session() as session:
        result = await fetch_full_channel(
            client, session, username="example_notes"
        )

    assert result.linked_chat_id == CHAT.id

    async with inventory.session() as session:
        stored = await session.get(RawChannel, PARENT.id)
        chat = await session.get(Channel, CHAT.id)

    assert stored is not None
    assert chat is None
