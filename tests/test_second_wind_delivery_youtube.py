"""The second wind of a YouTube list, delivered to its topic."""

from datetime import timedelta

from sqlalchemy import text
from test_weekly_delivery import (
    NOW,
    TOPIC_EN,
    WEEK,
    ListRecorder,
    a_video,
    channels,
    configured,  # noqa: F401 - the autouse fixture, applied here too
    deliver,
)
from yt_alert_seed import fake_video

from itgraph.bot.render import SECOND_WIND_VIDEOS
from itgraph.db.models import YtAlertRoute, YtLang
from itgraph.db.session import Database
from itgraph.db.yt_weekly import NewList, NewRevival, store_week

# The two readings `a_video` takes: at a day and at five days of age.
PUBLISHED = NOW - timedelta(days=6)


def a_revival(rank: int, channel: str, number: int) -> NewRevival:
    return NewRevival(
        rank=rank,
        channel_id=channel,
        video_id=fake_video(number),
        before_at=PUBLISHED,
        start_at=PUBLISHED + timedelta(days=1),
        end_at=PUBLISHED + timedelta(days=5),
        acceleration=None,
        share=1.4,
        others=0,
    )


async def a_list(database: Database, *revivals: NewRevival) -> None:
    """An English list with no entry, its second wind shown."""
    async with database.session() as session:
        await store_week(
            session,
            [
                NewList(
                    week_start=WEEK,
                    route=YtAlertRoute.EN,
                    computed_at=NOW,
                    threshold=2.0,
                    size=10,
                    comments_min=10,
                    videos_scored=40,
                    channels_above=0,
                    entries=(),
                    revivals=revivals,
                    second_wind_shown=True,
                )
            ],
        )


async def undelivered(database: Database) -> int:
    async with database.session() as session:
        return int(
            await session.scalar(
                text(
                    "SELECT count(*) FROM yt_weekly_revivals "
                    "WHERE delivered_at IS NULL"
                )
            )
        )


async def test_the_section_goes_to_its_topic_with_youtube_s_counts(
    database: Database,
) -> None:
    (channel,) = await channels(database, 1, YtLang.EN)
    await a_video(database, channel=channel, number=1)
    await a_list(database, a_revival(1, channel, 1))
    sender = ListRecorder()

    await deliver(database, sender)

    ((message, destination),) = sender.messages
    assert destination.topic_id == TOPIC_EN
    assert sender.previews == [False]
    assert "Выше нормы — ничего." in message
    assert f"{SECOND_WIND_VIDEOS}\n1. <b>A fake talk</b>" in message
    assert "8 200 просмотров" in message
    assert "12 345" in message
    assert "(+4 145)" in message
    assert "видео снова начало набирать просмотры" in message
    assert await undelivered(database) == 0


async def test_a_video_gone_before_sending_closes_the_ranks(
    database: Database,
) -> None:
    """YouTube stopped returning the first: its video is forgotten, its
    readings with it, and the second is numbered first."""
    first, second = await channels(database, 2, YtLang.EN)
    await a_video(database, channel=first, number=1, title="A first talk")
    await a_video(database, channel=second, number=2, title="A second talk")
    await a_list(database, a_revival(1, first, 1), a_revival(2, second, 2))
    async with database.session() as session:
        await session.execute(
            text("DELETE FROM yt_raw_videos WHERE video_id = :v"),
            {"v": fake_video(1)},
        )
    sender = ListRecorder()

    await deliver(database, sender)

    ((message, _),) = sender.messages
    section = message[message.index(SECOND_WIND_VIDEOS) :]
    assert "1. <b>A second talk</b>" in section
    assert "A first talk" not in message
    assert "\n2. " not in section
    assert await undelivered(database) == 0
