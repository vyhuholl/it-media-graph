"""The rising rankings' loader: both platforms' evidence in one snapshot.

A link through a stored lookup; an appearance read from two readings; a
seed whose list is out of date; the chart's Moscow day; and nothing
joined is stored. Every id, handle and post is invented, and nothing
touches a socket.
"""

from datetime import UTC, datetime, timedelta

from sqlalchemy import text
from tglinks import post, seed_linking, tg_channel, tg_posts
from ytfakes import (
    channel_id,
    sections_response,
    video_id,
    video_resource,
)
from ytinventory import channels, video

from itgraph.config import settings
from itgraph.db.models import (
    AffiliationDecision,
    CandidateOrigin,
    ChannelStatus,
    YtAffiliationCandidate,
    YtLookupKind,
)
from itgraph.db.session import Database
from itgraph.db.yt_chart import store_chart
from itgraph.db.yt_lookups import store_lookups
from itgraph.db.yt_rising import load_yt_rising_rows, moscow_day_start
from itgraph.db.yt_sections import store_sections
from itgraph.derive.yt_featured import featured_ids
from itgraph.derive.yt_rising import YtRising, YtRisingRows, rank_yt_rising

NOW = datetime.now(UTC)
SEED = ChannelStatus.SEED
TARGET = 90
OTHER = 91


def _rank(rows: YtRisingRows) -> YtRising:
    return rank_yt_rising(
        rows,
        NOW,
        window_days=settings.rising_window_days,
        min_families=settings.rising_min_families,
        answers_fresh_days=settings.youtube_answers_fresh_days,
    )


async def _seed_reading(
    database: Database, seed: int, listed: list[int], *, days_ago: float
) -> None:
    async with database.session() as session:
        await store_sections(
            session,
            channel_id(seed),
            sections_response([channel_id(n) for n in listed]),
            fetched_at=NOW - timedelta(days=days_ago),
        )


async def _given_video_reference(database: Database) -> None:
    """A YouTube seed whose video of three days ago names the target by
    a channel link in its description — one family of evidence."""
    await channels({1: SEED})(database)
    from itgraph.db.models import YtDiscoveryPath
    from itgraph.db.yt_videos import VideoWrite, store_videos

    async with database.session() as session:
        await store_videos(
            session,
            [
                VideoWrite(
                    video_id=video_id(2),
                    payload=video_resource(
                        2,
                        published=NOW - timedelta(days=3),
                        channel=1,
                        description=(
                            f"see https://youtube.com/channel/{channel_id(TARGET)}"
                        ),
                    ),
                    due_at=None,
                    reading=None,
                    discovered_via=YtDiscoveryPath.CHECK,
                )
            ],
            observed_at=NOW,
        )


async def _given_rising_shapes(database: Database) -> None:
    """The shapes every loader test starts from: `_given_video_reference`
    plus a Telegram seed whose post of five days ago links a video a
    stored lookup answers with the target — two families."""
    await _given_video_reference(database)
    await tg_channel(database, 2001)
    await tg_posts(
        database,
        2001,
        post(
            1,
            f"youtu.be/{video_id(1)}",
            date=(NOW - timedelta(days=5)).isoformat(),
        ),
    )
    async with database.session() as session:
        await store_lookups(
            session,
            {(YtLookupKind.VIDEO, video_id(1)): channel_id(TARGET)},
            fetched_at=NOW - timedelta(days=1),
        )


async def _rows(database: Database) -> YtRisingRows:
    async with database.session() as session:
        return await load_yt_rising_rows(session, now=NOW)


# --- the link ranking's evidence ---------------------------------------


async def test_a_link_through_a_video_s_lookup(database: Database) -> None:
    """A Telegram post's video link, answered by a stored lookup, and a
    YouTube seed's description naming the channel by id: two families,
    both dated by their content."""
    await _given_rising_shapes(database)

    rows = await _rows(database)
    ranked = _rank(rows)

    [rising] = ranked.links
    assert rising.channel_id == channel_id(TARGET)
    assert rising.families == 2
    # The post of five days ago is the earliest evidence in the window;
    # the video's reference is two days newer.
    assert rising.first_in_window == NOW - timedelta(days=5)


async def test_an_awaiting_lookup_names_no_channel(database: Database) -> None:
    """The same shapes with no lookup stored: the video link awaits one,
    counts as such, and names no channel — the target drops to one
    family and out of the ranking."""
    await seed_linking(database, 2001, f"youtu.be/{video_id(1)}")
    await channels({1: SEED})(database)
    await video(2, channel=1, description="see nothing")(database)

    rows = await _rows(database)
    ranked = _rank(rows)

    assert ranked.links == []
    assert ranked.awaiting_lookup == 1


async def test_two_seeds_of_one_family_are_one_family(
    database: Database,
) -> None:
    """A description reference from one seed and a new featuring from
    its confirmed family mate: one family, not rising alone."""
    await _given_video_reference(database)
    await channels({2: SEED})(database)
    async with database.session() as session:
        session.add(
            YtAffiliationCandidate(
                channel_a=channel_id(1),
                channel_b=channel_id(2),
                score=1.0,
                decision=AffiliationDecision.CONFIRMED,
                origin=CandidateOrigin.SIGNAL,
                decided_at=NOW,
            )
        )
    await _seed_reading(database, 2, [], days_ago=8)
    await _seed_reading(database, 2, [TARGET], days_ago=1)

    rows = await _rows(database)
    ranked = _rank(rows)

    # The featuring added nothing: same family as the description.
    assert ranked.links == []


async def test_an_older_answer_is_asked_again(database: Database) -> None:
    """A lookup older than `youtube_answers_fresh_days` is not an
    answer: the video awaits one again, and the channel loses the
    reference."""
    await _given_rising_shapes(database)
    async with database.session() as session:
        await session.execute(
            text(
                "UPDATE yt_raw_lookups SET fetched_at = "
                "'2026-10-01T00:00:00+00'"
            )
        )

    rows = await _rows(database)
    ranked = _rank(rows)

    assert ranked.awaiting_lookup == 1
    assert ranked.links == []


# --- appearances --------------------------------------------------------


async def test_an_appearance_from_two_readings(database: Database) -> None:
    """The target absent from the first reading, present in the second:
    an appearance, dated by the second — the second family the ranking
    needs."""
    await _given_rising_shapes(database)
    await _seed_reading(database, 1, [5], days_ago=8)
    await _seed_reading(database, 1, [5, TARGET], days_ago=1)

    rows = await _rows(database)
    ranked = _rank(rows)

    [rising] = ranked.links
    assert rising.channel_id == channel_id(TARGET)
    assert rising.families == 2
    # The earliest evidence in the window is the post of five days ago;
    # the appearance is dated by yesterday's reading.
    assert rising.first_in_window == NOW - timedelta(days=5)


async def test_the_first_reading_establishes_the_list(
    database: Database,
) -> None:
    """One reading listing five channels: none of the five is an
    appearance, and their ids still count as the standing queue reads
    them."""
    await _given_rising_shapes(database)
    await _seed_reading(database, 1, [1, 2, 3, 4, TARGET], days_ago=1)

    rows = await _rows(database)
    ranked = _rank(rows)

    # Only the two families of the links; the listing added nothing.
    [rising] = ranked.links
    assert rising.families == 2
    # The standing queue still reads the newest in-date list.
    from itgraph.db.yt_sections import featured_lists

    async with database.session() as session:
        lists = await featured_lists(session, now=NOW)
    assert featured_ids(lists[channel_id(1)]) == [
        channel_id(n) for n in (1, 2, 3, 4, TARGET)
    ]


async def test_an_out_of_date_list_contributes_nothing_and_is_counted(
    database: Database,
) -> None:
    """A seed whose only reading is older than the answers' freshness:
    no evidence from it — not even as an appearance, a fortiori — and
    the seed is counted among the lists out of date."""
    await _given_video_reference(database)
    await _seed_reading(database, 1, [TARGET], days_ago=31)

    rows = await _rows(database)
    ranked = _rank(rows)

    assert ranked.lists_out_of_date == 1
    assert ranked.links == []  # the description alone is one family


# --- the chart's day ----------------------------------------------------


async def test_the_chart_reads_each_regions_moscow_day(
    database: Database,
) -> None:
    """US read this morning and RU yesterday: US contributes its
    channels, RU contributes nothing and is said."""
    await channels({1: SEED})(database)
    today = moscow_day_start(NOW) + timedelta(hours=5)
    async with database.session() as session:
        await store_chart(
            session,
            "US",
            {
                "items": [
                    {
                        "id": video_id(1),
                        "snippet": {"channelId": channel_id(TARGET)},
                    }
                ]
            },
            fetched_at=today,
        )
        await store_chart(
            session,
            "RU",
            {
                "items": [
                    {
                        "id": video_id(2),
                        "snippet": {"channelId": channel_id(OTHER)},
                    }
                ]
            },
            fetched_at=today - timedelta(days=1),
        )

    rows = await _rows(database)
    ranked = _rank(rows)

    [only] = ranked.chart
    assert only.channel_id == channel_id(TARGET)
    assert only.region == "US"
    assert only.position == 1
    assert ranked.chart_without == ["RU"]


async def test_yesterdays_reading_of_a_region_is_not_todays(
    database: Database,
) -> None:
    await channels({1: SEED})(database)
    day_start = moscow_day_start(NOW)
    async with database.session() as session:
        await store_chart(
            session,
            "US",
            {
                "items": [
                    {
                        "id": video_id(1),
                        "snippet": {"channelId": channel_id(OTHER)},
                    }
                ]
            },
            fetched_at=day_start - timedelta(minutes=1),
        )
        await store_chart(
            session,
            "US",
            {
                "items": [
                    {
                        "id": video_id(2),
                        "snippet": {"channelId": channel_id(TARGET)},
                    }
                ]
            },
            fetched_at=day_start + timedelta(minutes=1),
        )

    rows = await _rows(database)
    ranked = _rank(rows)

    [only] = ranked.chart
    assert only.channel_id == channel_id(TARGET)


# --- nothing joined is stored ------------------------------------------


async def test_loading_changes_no_row(database: Database) -> None:
    """The ranking writes nothing: every table holds what it held."""
    await _given_rising_shapes(database)
    await _seed_reading(database, 1, [5], days_ago=8)
    await _seed_reading(database, 1, [5, TARGET], days_ago=1)
    async with database.session() as session:
        await store_chart(
            session, "US", {"items": []}, fetched_at=moscow_day_start(NOW)
        )

    async def counts() -> dict[str, int]:
        async with database.session() as session:
            tables = await session.execute(
                text(
                    "SELECT table_name FROM information_schema.tables "
                    "WHERE table_schema = 'public' ORDER BY table_name"
                )
            )
            out = {}
            for (table,) in tables:
                out[str(table)] = int(
                    await session.scalar(
                        text(f'SELECT COUNT(*) FROM "{table}"')
                    )
                )
            return out

    before = await counts()
    rows = await _rows(database)
    _rank(rows)
    after = await counts()

    assert before == after
