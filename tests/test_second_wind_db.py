"""The second wind's tables and queries, on both platforms."""

import json
from datetime import UTC, date, datetime, timedelta

from sqlalchemy import text

from itgraph.db import weekly as tg
from itgraph.db import yt_weekly as yt
from itgraph.db.models import YtAlertRoute, YtVideoFormat
from itgraph.db.session import Database

WEEK = date(2026, 11, 16)
START = datetime(2026, 11, 15, 21, 0, tzinfo=UTC)
BEFORE = START - timedelta(days=7)
END = START + timedelta(days=7)
COMPUTED = END + timedelta(hours=10)
CHANNEL = 8000000001
CHAT = 8000000002
YT_CHANNEL = "UCfakeSecondWindDb000001"
YT_OTHER = "UCfakeSecondWindDb000002"


async def run(database: Database, sql: str, **params: object) -> None:
    async with database.session() as session:
        await session.execute(text(sql), params)


async def tg_post(
    database: Database,
    channel: int,
    msg_id: int,
    published: datetime,
    *,
    album: int | None = None,
    readings: tuple[tuple[datetime, int], ...] = (),
) -> None:
    payload: dict[str, object] = {
        "_": "Message",
        "id": msg_id,
        "date": published.isoformat(),
    }
    if album is not None:
        payload["grouped_id"] = album
    await run(
        database,
        "INSERT INTO raw_messages (channel_id, msg_id, payload) "
        "VALUES (:c, :m, CAST(:p AS jsonb))",
        c=channel,
        m=msg_id,
        p=json.dumps(payload),
    )
    for at, views in readings:
        await run(
            database,
            "INSERT INTO message_metrics (channel_id, msg_id, observed_at, "
            "views) VALUES (:c, :m, :at, :v)",
            c=channel,
            m=msg_id,
            at=at,
            v=views,
        )


async def tg_channels(database: Database) -> None:
    for channel, chat in ((CHANNEL, False), (CHAT, True)):
        await run(
            database,
            "INSERT INTO channels (tg_id, username, title, discovered_via, "
            "status, kind, is_chat) VALUES (:c, :u, 'Fake', 'manual', 'seed', "
            "'personal', :chat)",
            c=channel,
            u=f"fake_wind_{channel}",
            chat=chat,
        )


READINGS = (
    (BEFORE - timedelta(days=1), 100),
    (BEFORE + timedelta(hours=1), 150),
    (START - timedelta(days=1), 800),
    (START + timedelta(days=3), 2000),
    (END + timedelta(hours=1), 9999),
)


async def test_posts_come_with_the_last_reading_at_or_before_each_moment(
    database: Database,
) -> None:
    await tg_channels(database)
    published = START - timedelta(days=20)
    await tg_post(database, CHANNEL, 10, published, readings=READINGS)
    await tg_post(database, CHANNEL, 11, published, album=5)
    await tg_post(database, CHAT, 12, published, readings=READINGS)
    await tg_post(database, CHANNEL, 13, START - timedelta(days=2))

    async with database.session() as session:
        found = await tg.posts_with_marks(
            session,
            since=START - timedelta(days=35),
            until=START - timedelta(days=7),
            before=BEFORE,
            start=START,
            end=END,
        )

    by_id = {post.msg_id: post for post in found}
    # The chat's post and the post too young are not among them; an
    # album's part is, for the scoring to collapse.
    assert sorted(by_id) == [10, 11]
    post = by_id[10]
    assert post.before == (BEFORE - timedelta(days=1), 100.0)
    assert post.start == (START - timedelta(days=1), 800.0)
    assert post.end == (START + timedelta(days=3), 2000.0)
    assert by_id[11].album == 5
    assert by_id[11].start is None


def a_tg_list(revival: tg.NewRevival | None = None) -> tg.NewList:
    return tg.NewList(
        week_start=WEEK,
        computed_at=COMPUTED,
        threshold=3.7,
        size=10,
        comments_min=10,
        posts_scored=0,
        channels_above=0,
        entries=(),
        revivals=() if revival is None else (revival,),
        second_wind_shown=True,
        second_wind_acceleration=3.0,
        second_wind_norm_share=0.5,
        second_wind_size=5,
    )


def a_tg_revival() -> tg.NewRevival:
    return tg.NewRevival(
        rank=1,
        channel_id=CHANNEL,
        msg_id=10,
        before_at=BEFORE - timedelta(days=1),
        start_at=START - timedelta(days=1),
        end_at=START + timedelta(days=3),
        previous_pace=87.5,
        pace=300.0,
        norm=1000.0,
        others=0,
    )


async def test_a_telegram_revival_is_stored_with_its_list_and_claimed(
    database: Database,
) -> None:
    await tg_channels(database)
    await tg_post(
        database, CHANNEL, 10, START - timedelta(days=20), readings=READINGS
    )

    async with database.session() as session:
        assert await tg.store_week(session, a_tg_list(a_tg_revival()))
    async with database.session() as session:
        assert not await tg.store_week(session, a_tg_list(a_tg_revival()))

    async with database.session() as session:
        [found] = await tg.claim_lists(session)
    assert found.second_wind_shown
    [revival] = found.revivals
    assert (revival.msg_id, revival.pace, revival.norm) == (10, 300.0, 1000.0)
    assert found.revivals_sent == 0

    async with database.session() as session:
        await tg.mark_revivals_delivered(session, found.id, [1], at=COMPUTED)
    async with database.session() as session:
        [again] = await tg.claim_lists(session)
    assert (again.revivals, again.revivals_sent) == ((), 1)


async def test_a_telegram_revival_whose_reading_is_gone_is_not_claimed(
    database: Database,
) -> None:
    await tg_channels(database)
    await tg_post(
        database, CHANNEL, 10, START - timedelta(days=20), readings=READINGS
    )
    async with database.session() as session:
        await tg.store_week(session, a_tg_list(a_tg_revival()))
    await run(
        database,
        "DELETE FROM message_metrics WHERE observed_at = :at",
        at=START + timedelta(days=3),
    )

    async with database.session() as session:
        [found] = await tg.claim_lists(session)
    assert found.revivals == ()


async def yt_setup(database: Database) -> None:
    for channel, lang in ((YT_CHANNEL, "en"), (YT_OTHER, "other")):
        await run(
            database,
            "INSERT INTO yt_channels (channel_id, status, kind, lang, "
            "discovered_via) VALUES (:c, 'seed', 'personal', :l, 'manual')",
            c=channel,
            l=lang,
        )
    published = (START - timedelta(days=60)).strftime("%Y-%m-%dT%H:%M:%SZ")
    videos = {
        "fakeWindLng": (YT_CHANNEL, {"duration": "PT20M"}, None),
        "fakeWindSht": (YT_CHANNEL, {"duration": "PT40S"}, None),
        "fakeWindLiv": (
            YT_CHANNEL,
            {"duration": "PT1H"},
            {"actualStartTime": published},
        ),
        "fakeWindOth": (YT_OTHER, {"duration": "PT20M"}, None),
    }
    for video_id, (channel, details, live) in videos.items():
        payload: dict[str, object] = {
            "snippet": {"channelId": channel, "publishedAt": published},
            "contentDetails": details,
        }
        if live is not None:
            payload["liveStreamingDetails"] = live
        await run(
            database,
            "INSERT INTO yt_raw_videos (video_id, payload, fetched_at, "
            "discovered_at, discovered_via) VALUES "
            "(:v, CAST(:p AS jsonb), now(), now(), 'backfill')",
            v=video_id,
            p=json.dumps(payload),
        )
        for at, views in READINGS:
            await run(
                database,
                "INSERT INTO yt_raw_video_stats (video_id, observed_at, views) "
                "VALUES (:v, :at, :n)",
                v=video_id,
                at=at,
                n=views,
            )


async def test_videos_come_with_their_format_and_three_readings(
    database: Database,
) -> None:
    await yt_setup(database)

    async with database.session() as session:
        found = await yt.videos_with_marks(
            session,
            since=START - timedelta(days=365),
            until=START - timedelta(days=7),
            before=BEFORE,
            start=START,
            end=END,
            short_max_seconds=180,
        )

    by_id = {video.video_id: video for video in found}
    # A channel labelled `other` is out of scope.
    assert sorted(by_id) == ["fakeWindLiv", "fakeWindLng", "fakeWindSht"]
    assert by_id["fakeWindLng"].format is YtVideoFormat.LONG
    assert by_id["fakeWindSht"].format is YtVideoFormat.SHORT
    assert by_id["fakeWindLiv"].format is None
    assert by_id["fakeWindLng"].end == (START + timedelta(days=3), 2000.0)


async def test_a_youtube_revival_is_stored_and_read_at_sending(
    database: Database,
) -> None:
    await yt_setup(database)
    revival = yt.NewRevival(
        rank=1,
        channel_id=YT_CHANNEL,
        video_id="fakeWindLng",
        before_at=BEFORE - timedelta(days=1),
        start_at=START - timedelta(days=1),
        end_at=START + timedelta(days=3),
        acceleration=3.4,
        share=2.1,
        others=0,
    )
    week = [
        yt.NewList(
            week_start=WEEK,
            route=YtAlertRoute.EN,
            computed_at=COMPUTED,
            threshold=2.0,
            size=10,
            comments_min=10,
            videos_scored=0,
            channels_above=0,
            entries=(),
            revivals=(revival,),
        )
    ]
    async with database.session() as session:
        assert await yt.store_week(session, week)

    async with database.session() as session:
        [found] = await yt.claim_lists(session, YtAlertRoute.EN)
        [pending] = found.revivals
        evidence = await yt.revival_evidence(session, pending)

    assert not found.second_wind_shown
    assert evidence is not None
    assert (evidence.start_views, evidence.end_views) == (800, 2000)

    await run(
        database,
        "DELETE FROM yt_raw_video_stats WHERE video_id = 'fakeWindLng' "
        "AND observed_at = :at",
        at=START - timedelta(days=1),
    )
    async with database.session() as session:
        [found] = await yt.claim_lists(session, YtAlertRoute.EN)
    assert found.revivals == ()
