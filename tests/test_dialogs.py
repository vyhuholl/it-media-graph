"""Importing the account's subscriptions into the inventory."""

from datetime import UTC, datetime
from typing import Any

from fakes import FakeTelegramClient
from sqlalchemy import select

from itgraph.db.models import (
    Channel,
    ChannelKind,
    ChannelStatus,
    DiscoverySource,
    RejectReason,
)
from itgraph.db.session import Database
from itgraph.tg.dialogs import ImportCounts, import_dialogs

# A direct message, a legacy group chat, and a channel with no username.
PRIVATE = {900000001, 1000000004, 1000000003}


async def test_first_run_populates_the_inventory(
    database: Database, telegram: FakeTelegramClient
) -> None:
    async with database.session() as session:
        counts = await import_dialogs(telegram, session)

    assert counts == ImportCounts(inserted=3, updated=0, skipped=3)

    async with database.session() as session:
        rows = (await session.scalars(select(Channel))).all()

    assert {row.tg_id for row in rows} == {
        1000000001,
        1000000002,
        1000000005,
    }
    assert all(row.status is ChannelStatus.CANDIDATE for row in rows)
    assert all(row.reviewed_at is None for row in rows)
    assert all(
        row.discovered_via is DiscoverySource.OWN_SUBSCRIPTIONS for row in rows
    )
    by_id = {row.tg_id: row for row in rows}
    assert by_id[1000000001].username == "example_notes"
    assert by_id[1000000001].is_chat is False
    # A public supergroup is a chat.
    assert by_id[1000000002].is_chat is True


async def test_private_dialogs_are_not_imported(
    database: Database, telegram: FakeTelegramClient
) -> None:
    async with database.session() as session:
        counts = await import_dialogs(telegram, session)

    async with database.session() as session:
        rows = (await session.scalars(select(Channel))).all()

    assert counts.skipped == len(PRIVATE)
    assert PRIVATE.isdisjoint({row.tg_id for row in rows})
    # Nothing publicly unaddressable made it in.
    assert all(row.username for row in rows)


async def test_rerun_preserves_review_work(
    database: Database,
    telegram: FakeTelegramClient,
    dialog_records: list[dict[str, Any]],
) -> None:
    async with database.session() as session:
        await import_dialogs(telegram, session)

    reviewed_at = datetime(2026, 1, 1, tzinfo=UTC)
    async with database.session() as session:
        channel = await session.get(Channel, 1000000001)
        assert channel is not None
        channel.status = ChannelStatus.REJECTED
        channel.reject_reason = RejectReason.CRYPTO
        channel.reject_note = "moved to trading calls"
        channel.kind = ChannelKind.MEDIA
        channel.reviewed_at = reviewed_at

    # Telegram now reports a new title and username for that channel.
    dialog_records[0]["title"] = "Example Notes (renamed)"
    dialog_records[0]["username"] = "example_notes_2"
    async with database.session() as session:
        counts = await import_dialogs(
            FakeTelegramClient(dialog_records), session
        )

    assert counts.inserted == 0
    assert counts.updated == 3

    async with database.session() as session:
        channel = await session.get(Channel, 1000000001)

    assert channel is not None
    assert channel.title == "Example Notes (renamed)"
    assert channel.username == "example_notes_2"
    # The review survived the re-import untouched.
    assert channel.status is ChannelStatus.REJECTED
    assert channel.reject_reason is RejectReason.CRYPTO
    assert channel.reject_note == "moved to trading calls"
    assert channel.kind is ChannelKind.MEDIA
    assert channel.reviewed_at == reviewed_at
    assert channel.discovered_via is DiscoverySource.OWN_SUBSCRIPTIONS


async def test_unsubscribing_keeps_the_record(
    database: Database,
    telegram: FakeTelegramClient,
    dialog_records: list[dict[str, Any]],
) -> None:
    async with database.session() as session:
        await import_dialogs(telegram, session)

    remaining = [r for r in dialog_records if r["id"] != 1000000005]
    async with database.session() as session:
        await import_dialogs(FakeTelegramClient(remaining), session)
        gone = await session.get(Channel, 1000000005)

    assert gone is not None
    assert gone.title == "Example Jobs"


async def test_a_duplicated_dialog_is_written_once(
    database: Database, dialog_records: list[dict[str, Any]]
) -> None:
    # Postgres refuses to let one ON CONFLICT statement touch a row
    # twice, so the helper has to collapse duplicates itself.
    doubled = [dialog_records[0], dialog_records[0]]
    async with database.session() as session:
        counts = await import_dialogs(FakeTelegramClient(doubled), session)

    assert counts.inserted == 1


async def test_an_empty_dialog_list_writes_nothing(
    database: Database,
) -> None:
    async with database.session() as session:
        counts = await import_dialogs(FakeTelegramClient([]), session)

    assert (counts.inserted, counts.updated) == (0, 0)
