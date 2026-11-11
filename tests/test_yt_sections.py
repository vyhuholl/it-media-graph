"""Featured lists: the request, the reading, and where the answer lives.

The request asks for the channel ids each section lists and nothing
else; any section's ids are featured, whatever its type; and a list is
replaced by every read.
"""

from datetime import UTC, datetime, timedelta

import httpx
import pytest
from sqlalchemy import select
from ytfakes import (
    FakeYouTube,
    channel_id,
    error_body,
    no_sleep,
    sections_response,
    use_key,
)

from itgraph.db.models import (
    ChannelStatus,
    YtChannel,
    YtChannelKind,
    YtCommand,
    YtLang,
    YtRawChannelSections,
    YtRejectReason,
)
from itgraph.db.session import Database
from itgraph.db.yt_channels import create_channels
from itgraph.db.yt_sections import (
    featured_lists,
    sections_due,
    seeds_without_list,
    store_sections,
)
from itgraph.derive.yt_featured import featured_ids
from itgraph.yt import sections
from itgraph.yt.client import youtube
from itgraph.yt.errors import ApiError

NOW = datetime.now(UTC)


@pytest.fixture(autouse=True)
def key(monkeypatch: pytest.MonkeyPatch) -> None:
    use_key(monkeypatch)


async def _ask(fake: FakeYouTube, database: Database) -> dict[str, object]:
    async with youtube(
        database, YtCommand.REFRESH, transport=fake.transport(), sleep=no_sleep
    ) as client:
        return await sections.of_channel(client, channel_id(1))


# --- the request -------------------------------------------------------


async def test_sections_are_asked_for_their_channel_ids_only(
    database: Database,
) -> None:
    fake = FakeYouTube(sections={channel_id(1): sections_response([], [])})

    await _ask(fake, database)

    [asked] = fake.calls("channelSections")
    assert asked == {
        "part": "contentDetails",
        "fields": "items(contentDetails/channels)",
        "channelId": channel_id(1),
    }
    # No type, title, playlists or position.
    assert "snippet" not in asked["part"] + asked["fields"]


async def test_a_channel_without_sections_is_an_empty_list(
    database: Database,
) -> None:
    fake = FakeYouTube()
    fake.queued = [
        httpx.Response(404, json=error_body(404, "channelNotFound"))
    ]

    assert await _ask(fake, database) == {"items": []}


async def test_sections_youtube_will_not_show_are_an_empty_list(
    database: Database,
) -> None:
    """One channel's refusal must not stop the refresh."""
    fake = FakeYouTube()
    fake.queued = [
        httpx.Response(403, json=error_body(403, "channelSectionForbidden"))
    ]

    assert await _ask(fake, database) == {"items": []}


async def test_any_other_refusal_stops(database: Database) -> None:
    fake = FakeYouTube()
    fake.queued = [
        httpx.Response(400, json=error_body(400, "invalidCriteria"))
    ]

    with pytest.raises(ApiError):
        await _ask(fake, database)


# --- any section's channels are featured -------------------------------


def test_a_shelf_among_other_sections() -> None:
    """Eight sections, as the probe found them: one lists two channels."""
    payload = sections_response(
        [], [], [], [channel_id(2), channel_id(3)], [], [], [], []
    )

    assert featured_ids(payload) == [channel_id(2), channel_id(3)]


def test_sections_of_an_undocumented_type_are_read_like_any_other() -> None:
    """Should YouTube send the type anyway — lowercase, or one the
    reference does not list — it changes nothing: only ids are read."""
    payload = {
        "items": [
            {"snippet": {"type": "recentuploads"}},
            {
                "snippet": {"type": "channelsectiontypeundefined"},
                "contentDetails": {"channels": [channel_id(4)]},
            },
            {
                "snippet": {"type": "multiplechannels"},
                "contentDetails": {"channels": [channel_id(2), channel_id(4)]},
            },
        ]
    }

    assert featured_ids(payload) == [channel_id(4), channel_id(2)]


@pytest.mark.parametrize("payload", [{}, {"items": []}, sections_response([])])
def test_no_shelf_is_no_channel(payload: dict[str, object]) -> None:
    assert featured_ids(payload) == []


def test_an_id_of_no_channel_shape_is_dropped() -> None:
    payload = {
        "items": [{"contentDetails": {"channels": ["not-a-channel", 7]}}]
    }

    assert featured_ids(payload) == []


# --- where a list lives ------------------------------------------------


async def _channels(
    database: Database, statuses: dict[int, ChannelStatus]
) -> None:
    async with database.session() as session:
        await create_channels(session, [channel_id(n) for n in statuses])
        for number, status in statuses.items():
            record = await session.get(YtChannel, channel_id(number))
            assert record is not None
            record.status = status
            record.kind = YtChannelKind.PERSONAL
            record.lang = YtLang.EN
            if status is ChannelStatus.REJECTED:
                record.reject_reason = YtRejectReason.NOT_IT


async def _store(
    database: Database, number: int, *listed: list[str], at: datetime = NOW
) -> None:
    async with database.session() as session:
        await store_sections(
            session,
            channel_id(number),
            sections_response(*listed),
            fetched_at=at,
        )


async def test_a_featured_list_holds_channel_ids_only(
    database: Database,
) -> None:
    await _channels(database, {1: ChannelStatus.SEED})
    await _store(database, 1, [channel_id(2)])

    async with database.session() as session:
        [row] = await session.scalars(select(YtRawChannelSections))
    assert row.channel_id == channel_id(1)
    assert featured_ids(row.payload) == [channel_id(2)]
    assert row.payload == sections_response([channel_id(2)])


async def test_a_newer_list_replaces_the_old_one(database: Database) -> None:
    await _channels(database, {1: ChannelStatus.SEED})
    await _store(database, 1, [channel_id(2)], at=NOW - timedelta(days=26))
    await _store(database, 1, [channel_id(3)])

    async with database.session() as session:
        rows = list(await session.scalars(select(YtRawChannelSections)))
    assert len(rows) == 1
    assert featured_ids(rows[0].payload) == [channel_id(3)]
    assert rows[0].fetched_at == NOW


async def test_seeds_due_are_those_without_a_fresh_list(
    database: Database,
) -> None:
    await _channels(
        database,
        {
            1: ChannelStatus.SEED,
            2: ChannelStatus.SEED,
            3: ChannelStatus.SEED,
            4: ChannelStatus.CANDIDATE,
            5: ChannelStatus.MAYBE,
            6: ChannelStatus.REJECTED,
        },
    )
    await _store(database, 2, [], at=NOW - timedelta(days=1))
    await _store(database, 3, [], at=NOW - timedelta(days=26))

    async with database.session() as session:
        due = await sections_due(session, older_than=NOW - timedelta(days=25))
    # Never read, and read too long ago; other statuses never.
    assert due == [channel_id(1), channel_id(3)]


async def test_a_gone_seed_is_not_due(database: Database) -> None:
    await _channels(database, {1: ChannelStatus.SEED})
    async with database.session() as session:
        record = await session.get(YtChannel, channel_id(1))
        assert record is not None
        record.gone_at = NOW

    async with database.session() as session:
        assert await sections_due(session, older_than=NOW) == []
        assert await seeds_without_list(session) == 0


async def test_only_seeds_lists_in_date_are_read(database: Database) -> None:
    await _channels(
        database,
        {
            1: ChannelStatus.SEED,
            2: ChannelStatus.REJECTED,
            3: ChannelStatus.SEED,
        },
    )
    await _store(database, 1, [channel_id(7)])
    await _store(database, 2, [channel_id(8)])  # a former seed's
    await _store(database, 3, [channel_id(9)], at=NOW - timedelta(days=31))

    async with database.session() as session:
        lists = await featured_lists(session)
        missing = await seeds_without_list(session)
    assert set(lists) == {channel_id(1)}
    assert missing == 1  # seed 3, whose list is no longer current
