"""A walk is kept only while the history it describes is.

Out of scope, gone, or — with the walk disabled — thirty days after it
ended, a channel's walk is forgotten, and the channel is walked anew
when it is next due. Tested through each door a channel leaves by: the
single `mark`, the review file, and a refresh that finds it gone.
"""

from datetime import UTC, datetime, timedelta

import pytest
from ytfakes import (
    FakeYouTube,
    channel_id,
    channel_resource,
    no_sleep,
    use_key,
)

from itgraph.db.models import (
    ChannelStatus,
    YtBackfillWalk,
    YtChannel,
    YtChannelKind,
    YtCommand,
    YtLang,
    YtRejectReason,
)
from itgraph.db.session import Database
from itgraph.db.yt_backfill import channels_to_walk, finish_walk, start_walk
from itgraph.db.yt_channels import (
    Review,
    apply_review,
    create_channels,
    mark_channel,
    store_payloads,
)
from itgraph.yt.client import youtube
from itgraph.yt.refresh import refresh_channels
from itgraph.yt_review import Decision, ParsedReview

NOW = datetime(2026, 11, 1, 12, 0, tzinfo=UTC)
SEED = Review(
    status=ChannelStatus.SEED, kind=YtChannelKind.PERSONAL, lang=YtLang.EN
)


async def _walked_seed(
    database: Database, number: int, *, fetched_at: datetime = NOW
) -> None:
    async with database.session() as session:
        await create_channels(session, [channel_id(number)])
        await store_payloads(
            session,
            {channel_id(number): channel_resource(number)},
            fetched_at=fetched_at,
        )
        await mark_channel(session, channel_id(number), SEED, now=NOW)
        await start_walk(
            session,
            channel_id(number),
            now=NOW,
            cutoff=NOW - timedelta(days=365),
            whole=False,
        )
        await finish_walk(session, channel_id(number), now=NOW, capped=False)


async def _walk(database: Database, number: int) -> YtBackfillWalk | None:
    async with database.session() as session:
        return await session.get(YtBackfillWalk, channel_id(number))


async def _due(database: Database) -> list[str]:
    async with database.session() as session:
        return [walk.channel_id for walk in await channels_to_walk(session)]


@pytest.mark.parametrize(
    "review",
    [
        Review(status=ChannelStatus.MAYBE),
        Review(status=ChannelStatus.REJECTED, reason=YtRejectReason.NOT_IT),
    ],
    ids=["maybe", "rejected"],
)
async def test_a_seed_taken_out_of_scope_by_mark_forgets_its_walk(
    database: Database, review: Review
) -> None:
    await _walked_seed(database, 1)

    async with database.session() as session:
        await mark_channel(session, channel_id(1), review, now=NOW)

    assert await _walk(database, 1) is None


async def test_a_seed_relabelled_keeps_its_walk(database: Database) -> None:
    await _walked_seed(database, 1)

    async with database.session() as session:
        await mark_channel(
            session,
            channel_id(1),
            Review(
                status=ChannelStatus.SEED,
                kind=YtChannelKind.COMPANY,
                lang=YtLang.RU,
            ),
            now=NOW,
        )

    assert await _walk(database, 1) is not None


async def test_a_seed_rejected_in_a_review_file_forgets_its_walk(
    database: Database,
) -> None:
    await _walked_seed(database, 1)
    parsed = ParsedReview(
        exported_at=NOW + timedelta(minutes=1),
        decisions=[
            Decision(
                where="line 5",
                channel_id=channel_id(1),
                status=ChannelStatus.REJECTED,
                kind=None,
                lang=None,
                reason=YtRejectReason.CONTENT_FARM,
                note=None,
            )
        ],
        skipped=0,
        problems=[],
    )

    async with database.session() as session:
        summary = await apply_review(session, parsed, now=NOW)

    assert len(summary.applied) == 1
    assert await _walk(database, 1) is None


async def test_a_channel_brought_back_into_scope_is_walked_again(
    database: Database,
) -> None:
    await _walked_seed(database, 1)
    async with database.session() as session:
        await mark_channel(
            session, channel_id(1), Review(status=ChannelStatus.MAYBE), now=NOW
        )
    assert await _due(database) == []

    async with database.session() as session:
        await mark_channel(session, channel_id(1), SEED, now=NOW)

    assert await _due(database) == [channel_id(1)]


async def test_a_refresh_that_finds_a_channel_gone_forgets_its_walk(
    database: Database, monkeypatch: pytest.MonkeyPatch
) -> None:
    use_key(monkeypatch)
    stale = datetime.now(UTC) - timedelta(days=26)
    await _walked_seed(database, 1, fetched_at=stale)
    await _walked_seed(database, 2, fetched_at=stale)
    fake = FakeYouTube(channel_resource(1))

    async with youtube(
        database,
        YtCommand.REFRESH,
        transport=fake.transport(),
        sleep=no_sleep,
    ) as yt:
        summary = await refresh_channels(yt, database)

    assert summary.gone == [channel_id(2)]
    assert await _walk(database, 1) is not None
    assert await _walk(database, 2) is None
    async with database.session() as session:
        record = await session.get(YtChannel, channel_id(2))
    assert record is not None and record.status is ChannelStatus.SEED
