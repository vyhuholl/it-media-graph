"""What YouTube said is kept 30 days at most; what the operator decided,
for good."""

from datetime import UTC, datetime, timedelta

from sqlalchemy import select, text
from ytfakes import channel_id, channel_resource

from itgraph.db.models import (
    Base,
    ChannelStatus,
    YtChannel,
    YtRawChannel,
    YtRejectReason,
)
from itgraph.db.session import Database
from itgraph.db.yt_channels import (
    create_channels,
    due_for_refresh,
    mark_gone,
    purge_expired,
    store_payloads,
)

NOW = datetime(2026, 10, 26, 12, 0, tzinfo=UTC)
TITLE = "Fake Channel With A Title Nobody Else Has"


async def _channel(
    database: Database, number: int, *, fetched_at: datetime | None = NOW
) -> None:
    async with database.session() as session:
        await create_channels(session, [channel_id(number)])
        if fetched_at is not None:
            await store_payloads(
                session,
                {channel_id(number): channel_resource(number, title=TITLE)},
                fetched_at=fetched_at,
            )


async def _payload(database: Database, number: int) -> YtRawChannel | None:
    async with database.session() as session:
        return await session.get(YtRawChannel, channel_id(number))


async def _record(database: Database, number: int) -> YtChannel | None:
    async with database.session() as session:
        return await session.get(YtChannel, channel_id(number))


async def _anywhere(database: Database, needle: str) -> list[str]:
    """Every table holding ``needle`` anywhere in any row."""
    holding = []
    async with database.session() as session:
        for table in Base.metadata.sorted_tables:
            found = await session.scalar(
                text(
                    f'SELECT count(*) FROM "{table.name}" t '
                    "WHERE row_to_json(t)::text LIKE :needle"
                ),
                {"needle": f"%{needle}%"},
            )
            if found:
                holding.append(table.name)
    return holding


async def test_an_expired_payload_is_removed_and_the_record_kept(
    database: Database,
) -> None:
    await _channel(database, 1, fetched_at=NOW - timedelta(days=31))
    await _channel(database, 2, fetched_at=NOW - timedelta(days=29))
    async with database.session() as session:
        record = await session.get(YtChannel, channel_id(1))
        assert record is not None
        record.status = ChannelStatus.REJECTED
        record.reject_reason = YtRejectReason.CONSUMER_TECH
        record.reviewed_at = NOW

    async with database.session() as session:
        assert await purge_expired(session, now=NOW) == 1

    assert await _payload(database, 1) is None
    assert await _payload(database, 2) is not None
    record = await _record(database, 1)
    assert record is not None
    assert record.reject_reason is YtRejectReason.CONSUMER_TECH


async def test_removing_the_payload_removes_the_data(
    database: Database,
) -> None:
    """The title is nowhere else to forget: not in the record, not in the
    journal, not in any table."""
    await _channel(database, 1, fetched_at=NOW - timedelta(days=31))
    assert await _anywhere(database, TITLE) == ["yt_raw_channels"]

    async with database.session() as session:
        await purge_expired(session, now=NOW)

    assert await _anywhere(database, TITLE) == []


async def test_a_new_payload_replaces_the_old(database: Database) -> None:
    await _channel(database, 1, fetched_at=NOW - timedelta(days=20))

    async with database.session() as session:
        await store_payloads(
            session,
            {channel_id(1): channel_resource(1, title="Renamed")},
            fetched_at=NOW,
        )

    async with database.session() as session:
        rows = (await session.scalars(select(YtRawChannel))).all()
    [row] = rows
    assert row.payload["snippet"]["title"] == "Renamed"
    assert row.fetched_at == NOW


async def test_a_channel_that_disappears(database: Database) -> None:
    await _channel(database, 1)

    async with database.session() as session:
        marked = await mark_gone(session, [channel_id(1)], now=NOW)

    assert marked == [channel_id(1)]
    assert await _payload(database, 1) is None
    record = await _record(database, 1)
    assert record is not None and record.gone_at == NOW
    assert await _anywhere(database, TITLE) == []


async def test_a_channel_gone_twice_keeps_its_first_date(
    database: Database,
) -> None:
    await _channel(database, 1)
    async with database.session() as session:
        await mark_gone(session, [channel_id(1)], now=NOW)
    async with database.session() as session:
        again = await mark_gone(
            session, [channel_id(1)], now=NOW + timedelta(days=1)
        )

    assert again == []
    record = await _record(database, 1)
    assert record is not None and record.gone_at == NOW


async def test_a_gone_channel_comes_back(database: Database) -> None:
    await _channel(database, 1)
    async with database.session() as session:
        await mark_gone(session, [channel_id(1)], now=NOW)

    async with database.session() as session:
        restored = await store_payloads(
            session,
            {channel_id(1): channel_resource(1)},
            fetched_at=NOW + timedelta(days=1),
        )

    assert restored == [channel_id(1)]
    record = await _record(database, 1)
    assert record is not None and record.gone_at is None
    assert await _payload(database, 1) is not None


async def test_what_is_due_for_refresh(database: Database) -> None:
    await _channel(database, 1, fetched_at=NOW - timedelta(days=26))
    await _channel(database, 2, fetched_at=NOW - timedelta(days=1))
    await _channel(database, 3, fetched_at=None)
    async with database.session() as session:
        rejected = await session.get(YtChannel, channel_id(1))
        assert rejected is not None
        rejected.status = ChannelStatus.REJECTED
        rejected.reject_reason = YtRejectReason.NOT_IT

    async with database.session() as session:
        due = await due_for_refresh(
            session, older_than=NOW - timedelta(days=25)
        )

    # Stale and rejected, and never fetched; not the fresh one.
    assert sorted(due) == [channel_id(1), channel_id(3)]


async def test_creating_leaves_an_existing_record_alone(
    database: Database,
) -> None:
    await _channel(database, 1)
    async with database.session() as session:
        record = await session.get(YtChannel, channel_id(1))
        assert record is not None
        record.status = ChannelStatus.MAYBE
        record.reviewed_at = NOW

    async with database.session() as session:
        created = await create_channels(
            session, [channel_id(1), channel_id(2), channel_id(2)]
        )

    assert created == [channel_id(2)]
    record = await _record(database, 1)
    assert record is not None and record.status is ChannelStatus.MAYBE
