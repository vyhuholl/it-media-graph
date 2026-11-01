"""The mention graph read out of the stored payloads.

Against the test database, because what is tested is which rows are
read — seeds' videos, by whichever command stored them — and that what
a refresh, an expiry or a removal did to the payloads shows at once,
with nothing written anywhere.
"""

from datetime import UTC, datetime, timedelta

from sqlalchemy import func, select, text
from ytfakes import (
    channel_id,
    channel_resource,
    sections_response,
    video_id,
    video_resource,
)

from itgraph.db.models import (
    Base,
    ChannelStatus,
    YtChannel,
    YtChannelKind,
    YtDiscoveryPath,
    YtLang,
    YtRejectReason,
)
from itgraph.db.session import Database
from itgraph.db.yt_channels import create_channels, store_payloads
from itgraph.db.yt_references import read_references
from itgraph.db.yt_sections import store_sections
from itgraph.db.yt_videos import (
    FoundVideo,
    VideoWrite,
    purge_expired_videos,
    replace_payloads,
    store_found_videos,
    store_videos,
)
from itgraph.derive.yt_references import (
    Form,
    Pending,
    PendingKind,
    References,
)

NOW = datetime.now(UTC)
PUBLISHED = NOW - timedelta(days=60)


async def _channels(
    database: Database, statuses: dict[int, ChannelStatus]
) -> None:
    """Channels with payloads, handle `fakehandle<n>`, in these statuses."""
    async with database.session() as session:
        await create_channels(session, [channel_id(n) for n in statuses])
        await store_payloads(
            session,
            {channel_id(n): channel_resource(n) for n in statuses},
            fetched_at=NOW,
        )
        for number, status in statuses.items():
            record = await session.get(YtChannel, channel_id(number))
            assert record is not None
            record.status = status
            record.kind = YtChannelKind.PERSONAL
            record.lang = YtLang.EN
            if status is ChannelStatus.REJECTED:
                record.reject_reason = YtRejectReason.NOT_IT


async def _loop_video(
    database: Database,
    number: int,
    *,
    channel: int,
    description: str,
    title: str | None = None,
    fetched_at: datetime = NOW,
) -> None:
    async with database.session() as session:
        await store_videos(
            session,
            [
                VideoWrite(
                    video_id=video_id(number),
                    payload=video_resource(
                        number,
                        published=PUBLISHED,
                        channel=channel,
                        title=title,
                        description=description,
                    ),
                    due_at=None,
                    reading=None,
                    discovered_via=YtDiscoveryPath.CHECK,
                )
            ],
            observed_at=fetched_at,
        )


async def _walk_video(
    database: Database, number: int, *, channel: int, description: str
) -> None:
    async with database.session() as session:
        await store_found_videos(
            session,
            [
                FoundVideo(
                    video_id=video_id(number),
                    payload=video_resource(
                        number,
                        published=PUBLISHED,
                        channel=channel,
                        description=description,
                    ),
                    due_at=None,
                    reading=None,
                )
            ],
            fetched_at=NOW,
        )


async def _read(database: Database) -> References:
    async with database.session() as session:
        return await read_references(session)


SEED = ChannelStatus.SEED


async def test_videos_found_by_the_poll_loop_and_the_walk(
    database: Database,
) -> None:
    await _channels(database, {1: SEED, 2: SEED, 3: SEED})
    await _loop_video(
        database, 1, channel=1, description="youtube.com/@fakehandle2"
    )
    await _walk_video(
        database,
        2,
        channel=1,
        description=f"youtube.com/channel/{channel_id(3)}",
    )

    references = await _read(database)

    assert (references.videos, references.seeds) == (2, 1)
    assert {(e.target_channel, e.form) for e in references.edges} == {
        (channel_id(2), Form.HANDLE_LINK),
        (channel_id(3), Form.ID_LINK),
    }


async def test_channels_out_of_scope_are_not_sources(
    database: Database,
) -> None:
    await _channels(
        database,
        {
            1: SEED,
            4: ChannelStatus.MAYBE,
            5: ChannelStatus.REJECTED,
            6: ChannelStatus.CANDIDATE,
        },
    )
    for number in (4, 5, 6):
        await _loop_video(
            database,
            number,
            channel=number,
            description="youtube.com/@fakehandle1 youtube.com/@fake-unknown",
        )

    references = await _read(database)

    assert references.videos == 0
    assert references.edges == []
    assert references.pending == {}


async def test_a_channel_description_is_not_a_source(
    database: Database,
) -> None:
    await _channels(database, {1: SEED, 2: SEED})
    async with database.session() as session:
        resource = channel_resource(1)
        resource["snippet"]["description"] = (
            "My co-host: youtube.com/@fakehandle2"
        )
        await store_payloads(
            session, {channel_id(1): resource}, fetched_at=NOW
        )

    references = await _read(database)

    assert references.edges == []


async def test_a_mention_in_a_title(database: Database) -> None:
    await _channels(database, {1: SEED, 2: SEED})
    await _loop_video(
        database, 1, channel=1, title="Guest @fakehandle2", description=""
    )

    references = await _read(database)

    assert [(e.target_channel, e.form) for e in references.edges] == [
        (channel_id(2), Form.MENTION)
    ]


async def test_a_rejected_channel_is_a_target(database: Database) -> None:
    await _channels(database, {1: SEED, 5: ChannelStatus.REJECTED})
    await _loop_video(
        database, 1, channel=1, description="Guest: @fakehandle5"
    )

    references = await _read(database)

    assert [(e.target_channel, e.form) for e in references.edges] == [
        (channel_id(5), Form.MENTION)
    ]


async def test_a_link_to_another_seed_s_stored_video(
    database: Database,
) -> None:
    await _channels(database, {1: SEED, 2: SEED})
    await _loop_video(database, 2, channel=2, description="")
    await _loop_video(
        database, 1, channel=1, description=f"See youtu.be/{video_id(2)}"
    )

    references = await _read(database)

    [edge] = references.edges
    assert (edge.target_channel, edge.target_video) == (
        channel_id(2),
        video_id(2),
    )
    assert edge.published_at is not None


async def test_a_description_edited_upstream(database: Database) -> None:
    await _channels(database, {1: SEED, 2: SEED})
    await _loop_video(
        database, 1, channel=1, description="youtube.com/@fakehandle2"
    )
    assert len((await _read(database)).edges) == 1

    async with database.session() as session:
        await replace_payloads(
            session,
            {
                video_id(1): video_resource(
                    1,
                    published=PUBLISHED,
                    channel=1,
                    description="Nothing linked any more.",
                )
            },
            fetched_at=NOW,
        )

    assert (await _read(database)).edges == []


async def test_an_expired_video_takes_its_pending_handle(
    database: Database,
) -> None:
    await _channels(database, {1: SEED})
    await _loop_video(
        database,
        1,
        channel=1,
        description="youtube.com/@fake-unknown",
        fetched_at=NOW - timedelta(days=31),
    )
    handle = Pending(PendingKind.HANDLE, "fake-unknown")
    assert handle in (await _read(database)).pending

    async with database.session() as session:
        await purge_expired_videos(session)

    assert handle not in (await _read(database)).pending


async def _state(database: Database) -> dict[str, object]:
    """Every table's row count, and a digest of every payload."""
    state: dict[str, object] = {}
    async with database.session() as session:
        for table in Base.metadata.sorted_tables:
            state[table.name] = await session.scalar(
                select(func.count()).select_from(table)
            )
        for table in ("yt_raw_videos", "yt_raw_channels"):
            state[f"{table} payloads"] = await session.scalar(
                text(
                    f"SELECT md5(string_agg(payload::text, '' ORDER BY 1)) "
                    f"FROM {table}"
                )
            )
    return state


async def test_reading_writes_nothing(database: Database) -> None:
    await _channels(database, {1: SEED, 2: SEED})
    await _loop_video(
        database,
        1,
        channel=1,
        description="youtube.com/@fakehandle2 youtube.com/@fake-unknown",
    )
    before = await _state(database)

    await _read(database)

    assert await _state(database) == before


# --- featured lists ----------------------------------------------------


async def _featured(
    database: Database,
    number: int,
    *listed: str,
    fetched_at: datetime = NOW,
) -> None:
    async with database.session() as session:
        await store_sections(
            session,
            channel_id(number),
            sections_response(list(listed)),
            fetched_at=fetched_at,
        )


async def test_a_seeds_featured_list_is_a_source(database: Database) -> None:
    await _channels(database, {1: SEED, 2: SEED})
    await _featured(database, 1, channel_id(2), channel_id(9))

    references = await _read(database)

    [edge] = references.edges
    assert (edge.channel_id, edge.target_channel, edge.form) == (
        channel_id(1),
        channel_id(2),
        Form.FEATURED,
    )
    assert (edge.video_id, edge.target_video) == (None, None)
    pending = references.pending[Pending(PendingKind.CHANNEL, channel_id(9))]
    assert pending.featuring == {channel_id(1)}
    assert references.featured_lists == 1


async def test_a_former_seeds_featured_list_is_not_read(
    database: Database,
) -> None:
    await _channels(database, {1: ChannelStatus.REJECTED})
    await _featured(database, 1, channel_id(9))

    references = await _read(database)

    assert references.pending == {}
    assert references.featured_lists == 0


async def test_an_expired_featured_list_names_nothing(
    database: Database,
) -> None:
    await _channels(database, {1: SEED})
    await _featured(
        database, 1, channel_id(9), fetched_at=NOW - timedelta(days=31)
    )

    references = await _read(database)

    assert references.pending == {}
