"""YouTube's alerts: the queue, the topics, the wording.

Against a real Postgres, because routing is a join through the video to
its channel's current label, and an alert's life is a chain of cascades —
a fake would prove neither. Everything here is synthetic and looks it.
"""

from dataclasses import replace
from datetime import UTC, datetime, timedelta

import pytest
from sqlalchemy import text
from test_bot import Recorder
from yt_alert_seed import delivery_of, fake_video, seed_alert, seed_channel

from itgraph.bot.app import BotStats, deliver_once
from itgraph.bot.destinations import (
    Destination,
    Platform,
    youtube_destinations,
)
from itgraph.bot.queues import TelegramQueue, YoutubeQueue
from itgraph.bot.render import MESSAGE_LIMIT, render_youtube, units
from itgraph.config import settings
from itgraph.db import yt_alerts
from itgraph.db.models import (
    YtAlertKind,
    YtAlertRoute,
    YtLang,
)
from itgraph.db.session import Database
from itgraph.db.yt_videos import remove_videos
from itgraph.derive.yt_videos import Counters

NOW = datetime(2026, 11, 11, 12, 0, tzinfo=UTC)
CHAT = -100000000000017
TOPIC_EN = 11
TOPIC_RU = 12


@pytest.fixture(autouse=True)
def configured(monkeypatch: pytest.MonkeyPatch) -> None:
    """YouTube's chat and both topics, quiet hours off, the digest far."""
    monkeypatch.setattr(settings, "alert_chat_id", None)
    monkeypatch.setattr(settings, "youtube_alert_chat_id", CHAT)
    monkeypatch.setattr(settings, "youtube_alert_topic_en", TOPIC_EN)
    monkeypatch.setattr(settings, "youtube_alert_topic_ru", TOPIC_RU)
    monkeypatch.setattr(settings, "youtube_alert_quiet_from_hour", 0)
    monkeypatch.setattr(settings, "youtube_alert_quiet_to_hour", 0)
    monkeypatch.setattr(settings, "youtube_alert_digest_hour", 23)
    monkeypatch.setattr(settings, "youtube_alert_timezone", "UTC")


def topics(sender: Recorder) -> list[int | None]:
    return [destination.topic_id for _, destination in sender.messages]


async def deliver(
    database: Database, sender: Recorder, *, now: datetime = NOW
) -> BotStats:
    stats = BotStats()
    await deliver_once(database, sender, stats, now=now)
    return stats


# --- routing --------------------------------------------------------


async def test_each_language_goes_to_its_topic(database: Database) -> None:
    async with database.session() as session:
        en = await seed_channel(session, 1, lang=YtLang.EN, fetched_at=NOW)
        ru = await seed_channel(session, 2, lang=YtLang.RU, fetched_at=NOW)
        await seed_alert(session, channel_id=en, video=1, now=NOW)
        await seed_alert(session, channel_id=ru, video=2, now=NOW)
    sender = Recorder()

    await deliver(database, sender)

    assert sorted(topics(sender)) == [TOPIC_EN, TOPIC_RU]
    assert {d.chat_id for _, d in sender.messages} == {CHAT}
    async with database.session() as session:
        assert await delivery_of(session) == {
            fake_video(1): ("direct", "en"),
            fake_video(2): ("direct", "ru"),
        }


async def test_the_label_at_sending_decides(database: Database) -> None:
    """Relabelled while it waited for the digest: it follows the label."""
    async with database.session() as session:
        channel = await seed_channel(
            session, 1, lang=YtLang.EN, fetched_at=NOW
        )
        alert = await seed_alert(session, channel_id=channel, video=1, now=NOW)
        await session.execute(
            text("UPDATE yt_alerts SET held_at = :at WHERE id = :id"),
            {"at": NOW, "id": alert},
        )
        await session.execute(text("UPDATE yt_channels SET lang = 'ru'"))
    sender = Recorder()

    await deliver(database, sender, now=NOW.replace(hour=23, minute=30))

    assert topics(sender) == [TOPIC_RU]
    assert "📋" in sender.messages[0][0]
    async with database.session() as session:
        assert (await delivery_of(session))[fake_video(1)] == ("digest", "ru")


async def test_a_channel_out_of_scope_since_still_gets_its_alert(
    database: Database,
) -> None:
    async with database.session() as session:
        channel = await seed_channel(
            session, 1, lang=YtLang.RU, fetched_at=NOW
        )
        await seed_alert(session, channel_id=channel, video=1, now=NOW)
        await session.execute(
            text(
                "UPDATE yt_channels SET status = 'rejected', "
                "reject_reason = 'not_it'"
            )
        )
    sender = Recorder()

    await deliver(database, sender)

    assert topics(sender) == [TOPIC_RU]


async def test_another_language_is_excluded_not_sent(
    database: Database,
) -> None:
    async with database.session() as session:
        channel = await seed_channel(
            session, 1, lang=YtLang.EN, fetched_at=NOW
        )
        head = await seed_alert(session, channel_id=channel, video=1, now=NOW)
        await seed_alert(
            session, channel_id=channel, video=2, now=NOW, folded_into=head
        )
        await session.execute(text("UPDATE yt_channels SET lang = 'other'"))
    sender = Recorder()

    stats = await deliver(database, sender)

    assert sender.messages == []
    assert stats.excluded == 1
    async with database.session() as session:
        assert await delivery_of(session) == {
            fake_video(1): ("excluded", None),
            fake_video(2): ("excluded", None),
        }
        assert (await yt_alerts.outstanding(session)).total == 0
        totals, _ = await yt_alerts.route_counts(session, failing_after=3)
    assert totals["excluded"] == 1


async def test_a_video_no_inventory_channel_owns_is_excluded(
    database: Database,
) -> None:
    """Reattributed by YouTube to a channel not in the inventory: there is
    no label to route by, and waiting forever would read as stuck."""
    async with database.session() as session:
        channel = await seed_channel(
            session, 1, lang=YtLang.EN, fetched_at=NOW
        )
        await seed_alert(session, channel_id=channel, video=1, now=NOW)
        await session.execute(
            text(
                "UPDATE yt_raw_videos SET payload = jsonb_set(payload, "
                "'{snippet,channelId}', '\"UCfakeChannel99999999999\"')"
            )
        )
    sender = Recorder()

    await deliver(database, sender)

    assert sender.messages == []
    async with database.session() as session:
        assert (await delivery_of(session))[fake_video(1)][0] == "excluded"


async def test_without_youtube_configured_nothing_is_sent(
    database: Database, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(settings, "youtube_alert_chat_id", None)
    monkeypatch.setattr(settings, "youtube_alert_topic_en", None)
    monkeypatch.setattr(settings, "youtube_alert_topic_ru", None)
    async with database.session() as session:
        channel = await seed_channel(
            session, 1, lang=YtLang.EN, fetched_at=NOW
        )
        await seed_alert(session, channel_id=channel, video=1, now=NOW)
    sender = Recorder()

    await deliver(database, sender)

    assert sender.messages == []
    async with database.session() as session:
        assert (await yt_alerts.outstanding(session)).total == 1


# --- each destination's account -------------------------------------


async def test_one_topics_cap_spends_nothing_of_the_other(
    database: Database, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(settings, "youtube_alert_daily_cap", 1)
    async with database.session() as session:
        en = await seed_channel(session, 1, lang=YtLang.EN, fetched_at=NOW)
        ru = await seed_channel(session, 2, lang=YtLang.RU, fetched_at=NOW)
        for video in (1, 2):
            await seed_alert(session, channel_id=en, video=video, now=NOW)
        await seed_alert(session, channel_id=ru, video=3, now=NOW)
    sender = Recorder()

    await deliver(database, sender)

    assert sorted(topics(sender)) == [TOPIC_EN, TOPIC_RU]
    async with database.session() as session:
        _, waiting = await yt_alerts.route_counts(session, failing_after=3)
    assert waiting[YtAlertRoute.EN].held == 1
    assert waiting[YtAlertRoute.RU].undelivered == 0


async def test_telegrams_cap_spends_nothing_of_youtubes(
    database: Database, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A burst on Telegram does not hold YouTube's alerts back."""
    from test_alerts_db import seed

    from itgraph.alerts.cascade import Cascade
    from itgraph.db.alerts import raise_cascades

    monkeypatch.setattr(settings, "alert_chat_id", 4242)
    monkeypatch.setattr(settings, "alert_daily_cap", 1)
    monkeypatch.setattr(settings, "alert_quiet_from_hour", 0)
    monkeypatch.setattr(settings, "alert_quiet_to_hour", 0)
    monkeypatch.setattr(settings, "alert_digest_hour", 23)
    monkeypatch.setattr(settings, "watch_timezone", "UTC")
    await seed(database, reposters=3)
    async with database.session() as session:
        await raise_cascades(
            session,
            [
                Cascade(post_key=(1000000001, 500), band=2, value=2),
                Cascade(post_key=(1000000001, 500), band=3, value=3),
            ],
        )
        channel = await seed_channel(
            session, 1, lang=YtLang.EN, fetched_at=NOW
        )
        await seed_alert(session, channel_id=channel, video=1, now=NOW)
    sender = Recorder()

    await deliver(database, sender)

    platforms = [d.platform for _, d in sender.messages]
    assert platforms.count(Platform.TELEGRAM) == 1
    assert platforms.count(Platform.YOUTUBE) == 1


async def test_each_topic_gets_a_summary_of_its_own(
    database: Database, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(settings, "youtube_alert_quiet_from_hour", 2)
    monkeypatch.setattr(settings, "youtube_alert_quiet_to_hour", 7)
    monkeypatch.setattr(settings, "youtube_alert_digest_hour", 9)
    night = NOW.replace(hour=3)
    async with database.session() as session:
        en = await seed_channel(session, 1, lang=YtLang.EN, fetched_at=night)
        ru = await seed_channel(session, 2, lang=YtLang.RU, fetched_at=night)
        await seed_alert(session, channel_id=en, video=1, now=night)
        await seed_alert(session, channel_id=en, video=2, now=night)
        await seed_alert(session, channel_id=ru, video=3, now=night)
    sender = Recorder()
    stats = BotStats()
    await deliver_once(database, sender, stats, now=night)
    assert sender.messages == []

    await deliver_once(database, sender, stats, now=NOW.replace(hour=9))

    by_topic = {d.topic_id: text for text, d in sender.messages}
    assert sorted(by_topic) == [TOPIC_EN, TOPIC_RU]
    assert "2 оповещение" in by_topic[TOPIC_EN]
    assert by_topic[TOPIC_EN].count("▶️ YouTube") == 2
    assert fake_video(3) in by_topic[TOPIC_RU]
    assert fake_video(3) not in by_topic[TOPIC_EN]


# --- failing and missing --------------------------------------------


class RefusesTopic(Recorder):
    """Telegram refusing one topic, as a deleted one is refused."""

    def __init__(self, topic: int) -> None:
        super().__init__()
        self.topic = topic

    async def send(self, text: str, *, destination: Destination) -> None:
        if destination.topic_id == self.topic:
            raise RuntimeError("Bad Request: message thread not found")
        await super().send(text, destination=destination)


async def test_a_refused_topic_fails_and_goes_nowhere_else(
    database: Database,
) -> None:
    async with database.session() as session:
        channel = await seed_channel(
            session, 1, lang=YtLang.EN, fetched_at=NOW
        )
        await seed_alert(session, channel_id=channel, video=1, now=NOW)
    sender = RefusesTopic(TOPIC_EN)

    for _ in range(3):
        await deliver(database, sender)

    assert sender.messages == []
    async with database.session() as session:
        assert await yt_alerts.failing(session, attempts=3) == 1
        _, waiting = await yt_alerts.route_counts(session, failing_after=3)
    assert waiting[YtAlertRoute.EN].failing == 1
    assert waiting[YtAlertRoute.RU].failing == 0


async def test_an_old_channel_payload_still_names_the_channel(
    database: Database,
) -> None:
    async with database.session() as session:
        channel = await seed_channel(
            session,
            1,
            lang=YtLang.EN,
            fetched_at=NOW - timedelta(days=31),
            title="A stale fake channel",
        )
        await seed_alert(session, channel_id=channel, video=1, now=NOW)
    sender = Recorder()

    await deliver(database, sender)

    ((message, _),) = sender.messages
    assert "A stale fake channel" in message
    assert "<b>A fake video's title</b>" in message


async def test_an_old_video_payload_is_shown(database: Database) -> None:
    async with database.session() as session:
        channel = await seed_channel(
            session, 1, lang=YtLang.EN, fetched_at=NOW
        )
        await seed_alert(
            session,
            channel_id=channel,
            video=1,
            now=NOW,
            fetched_ago=timedelta(days=31),
        )
    sender = Recorder()

    await deliver(database, sender)

    ((message, _),) = sender.messages
    assert "<b>A fake video's title</b>" in message


# --- the queue's life -----------------------------------------------


async def test_an_alert_goes_when_youtube_stops_returning_its_video(
    database: Database,
) -> None:
    async with database.session() as session:
        channel = await seed_channel(
            session, 1, lang=YtLang.EN, fetched_at=NOW
        )
        await seed_alert(session, channel_id=channel, video=1, now=NOW)
        await remove_videos(session, [fake_video(1)])
        assert await delivery_of(session) == {}


async def test_a_head_takes_its_folds(database: Database) -> None:
    async with database.session() as session:
        channel = await seed_channel(
            session, 1, lang=YtLang.EN, fetched_at=NOW
        )
        head = await seed_alert(session, channel_id=channel, video=1, now=NOW)
        for video in (2, 3):
            await seed_alert(
                session,
                channel_id=channel,
                video=video,
                now=NOW,
                folded_into=head,
            )
        await remove_videos(session, [fake_video(1)])
        assert await delivery_of(session) == {}


async def test_the_head_is_told_and_its_folds_settled(
    database: Database,
) -> None:
    async with database.session() as session:
        channel = await seed_channel(
            session, 1, lang=YtLang.RU, fetched_at=NOW
        )
        head = await seed_alert(session, channel_id=channel, video=1, now=NOW)
        for video in (2, 3):
            await seed_alert(
                session,
                channel_id=channel,
                video=video,
                now=NOW,
                folded_into=head,
            )
    sender = Recorder()

    await deliver(database, sender)

    ((message, _),) = sender.messages
    assert "ещё 2 видео канала" in message
    async with database.session() as session:
        assert set((await delivery_of(session)).values()) == {
            ("direct", "ru"),
            ("folded", "ru"),
        }


async def test_routes_are_claimed_apart(database: Database) -> None:
    async with database.session() as session:
        en = await seed_channel(session, 1, lang=YtLang.EN, fetched_at=NOW)
        ru = await seed_channel(session, 2, lang=YtLang.RU, fetched_at=NOW)
        await seed_alert(session, channel_id=en, video=1, now=NOW)
        await seed_alert(session, channel_id=ru, video=2, now=NOW)

        claimed = await yt_alerts.claim(
            session, YtAlertRoute.RU, held=False, limit=None
        )

    assert [alert.video_id for alert in claimed] == [fake_video(2)]


# --- the wording ----------------------------------------------------


def youtube(**overrides: object) -> str:
    kwargs: dict[str, object] = {
        "alert_id": 1,
        "kind": YtAlertKind.VIEWS_SPIKE,
        "route": YtAlertRoute.EN,
        "video_id": "fakeVid0001",
        "title": "A fake video's title",
        "channel_title": "A fake channel",
        "started_at": NOW - timedelta(hours=5),
        "counters": Counters(views=12400, likes=830, comments=95),
        "counted_at": NOW - timedelta(minutes=15),
        "z": 4.2,
        "scored_at": NOW - timedelta(hours=2),
        "now": NOW,
        "zone": "Europe/Moscow",
    }
    kwargs.update(overrides)
    return render_youtube(**kwargs).text  # type: ignore[arg-type]


def test_a_youtube_alert_names_its_source_and_links_to_the_video() -> None:
    text = youtube()

    assert text.splitlines()[0] == "▶️ YouTube · EN"
    assert "https://www.youtube.com/watch?v=fakeVid0001" in text
    assert "<b>A fake video's title</b> — A fake channel" in text


def test_the_counts_are_dated_and_the_score_is_itgraphs() -> None:
    text = youtube()

    assert (
        "Данные YouTube на 14:45 MSK: просмотры 12 400 · лайки 830 · "
        "комментарии 95"
    ) in text
    assert "Оценка itgraph на 13:00 MSK" in text
    assert "рассчитана itgraph, не метрика YouTube" in text
    assert "z 4.2" in text


def test_an_older_moment_carries_its_date() -> None:
    text = youtube(counted_at=NOW - timedelta(days=2))
    assert "Данные YouTube на 09.11 15:00 MSK" in text


def test_a_counter_youtube_did_not_report_is_left_out() -> None:
    text = youtube(counters=Counters(views=12400, likes=None, comments=95))

    assert "лайки" not in text
    assert "просмотры 12 400 · комментарии 95" in text


def test_no_reading_shows_no_counts_and_still_the_score() -> None:
    text = youtube(counters=Counters(), counted_at=None)

    assert "Данные YouTube" not in text
    assert "Оценка itgraph" in text


def test_a_comment_alert_states_the_count() -> None:
    text = youtube(kind=YtAlertKind.COMMENT_SPIKE, observed=117, expected=0.5)
    assert "💬 Комментарии — 117, обычно меньше одного" in text


def test_a_like_alert_says_likes() -> None:
    assert "👍 Лайки — намного выше обычного для канала" in youtube(
        kind=YtAlertKind.LIKE_SPIKE
    )


def test_a_title_is_escaped() -> None:
    text = youtube(title="Tips & <tricks>", channel_title="R&D")
    assert "Tips &amp; &lt;tricks&gt;" in text
    assert "R&amp;D" in text


def test_the_longest_youtube_alert_fits() -> None:
    text = youtube(
        title="T" * 100, channel_title="C" * 100, folded=999, z=40.0
    )
    assert units(text) <= MESSAGE_LIMIT


def test_a_broadcasts_age_counts_from_its_start() -> None:
    """The evidence hands over the actual start where there is one."""
    assert "опубликовано 2 ч 00 мин назад" in youtube(
        started_at=NOW - timedelta(hours=2)
    )


async def test_the_evidence_takes_a_broadcasts_start(
    database: Database,
) -> None:
    async with database.session() as session:
        channel = await seed_channel(
            session, 1, lang=YtLang.EN, fetched_at=NOW
        )
        alert = await seed_alert(session, channel_id=channel, video=1, now=NOW)
        started = NOW - timedelta(hours=2)
        await session.execute(
            text(
                "UPDATE yt_raw_videos SET payload = payload || "
                "jsonb_build_object('liveStreamingDetails', "
                "jsonb_build_object('actualStartTime', CAST(:at AS text)))"
            ),
            {"at": started.isoformat()},
        )
        (pending,) = await yt_alerts.claim(
            session, YtAlertRoute.EN, held=False, limit=None
        )
        evidence = await yt_alerts.yt_alert_evidence(session, pending)

    assert pending.id == alert
    assert evidence is not None
    assert evidence.started_at == started


def test_the_destinations_follow_the_settings() -> None:
    routes = youtube_destinations(settings)

    assert {route: d.topic_id for route, d in routes.items()} == {
        YtAlertRoute.EN: TOPIC_EN,
        YtAlertRoute.RU: TOPIC_RU,
    }
    assert all(d.chat_id == CHAT for d in routes.values())


def test_the_queues_know_their_platforms() -> None:
    assert TelegramQueue.platform is Platform.TELEGRAM
    assert YoutubeQueue.platform is Platform.YOUTUBE


async def test_a_youtube_digest_names_youtube_on_every_entry(
    database: Database, monkeypatch: pytest.MonkeyPatch
) -> None:
    """No one attribution for a whole list of results."""
    monkeypatch.setattr(settings, "youtube_alert_digest_hour", 9)
    async with database.session() as session:
        channel = await seed_channel(
            session, 1, lang=YtLang.EN, fetched_at=NOW
        )
        for video in (1, 2, 3):
            alert = await seed_alert(
                session, channel_id=channel, video=video, now=NOW
            )
            await session.execute(
                text("UPDATE yt_alerts SET held_at = :at WHERE id = :id"),
                {"at": NOW, "id": alert},
            )
    sender = Recorder()

    await deliver(database, sender, now=NOW.replace(hour=9, minute=5))

    ((message, destination),) = sender.messages
    assert destination == replace(destination, topic_id=TOPIC_EN)
    assert message.count("▶️ YouTube · EN") == 3
    assert message.count("https://www.youtube.com/watch?v=") == 3


async def test_the_counts_shown_are_the_newest_stored(
    database: Database,
) -> None:
    """Scored at one reading, sent after another: the later one is shown,
    with its moment, and the score keeps its own."""
    async with database.session() as session:
        channel = await seed_channel(
            session, 1, lang=YtLang.EN, fetched_at=NOW
        )
        await seed_alert(
            session,
            channel_id=channel,
            video=1,
            now=NOW,
            readings=(
                (timedelta(hours=2), 100, 10, 1),
                (timedelta(minutes=15), 900, 90, 9),
            ),
        )
    sender = Recorder()

    await deliver(database, sender)

    ((message, _),) = sender.messages
    assert "Данные YouTube на 11:45 UTC: просмотры 900" in message
    assert "Оценка itgraph на 10:00 UTC" in message


async def test_an_alert_row_holds_nothing_youtube_said(
    database: Database,
) -> None:
    async with database.session() as session:
        columns = set(
            await session.scalars(
                text(
                    "SELECT column_name FROM information_schema.columns "
                    "WHERE table_name = 'yt_alerts'"
                )
            )
        )

    assert columns == {
        "id",
        "kind",
        "video_id",
        "scored_at",
        "band",
        "value",
        "expected",
        "raised_at",
        "held_at",
        "delivered_at",
        "delivery",
        "route",
        "attempts",
        "last_error",
        "folded_into",
    }


async def test_a_comment_alert_counts_from_the_reading_it_was_scored_on(
    database: Database,
) -> None:
    """The count that crossed, not the newest: the score is about it."""
    async with database.session() as session:
        channel = await seed_channel(
            session, 1, lang=YtLang.EN, fetched_at=NOW
        )
        await seed_alert(
            session,
            channel_id=channel,
            video=1,
            now=NOW,
            kind=YtAlertKind.COMMENT_SPIKE,
            expected=0.5,
            readings=(
                (timedelta(hours=2), 100, 1, 17),
                (timedelta(minutes=15), 900, 9, 40),
            ),
        )
    sender = Recorder()

    await deliver(database, sender)

    ((message, _),) = sender.messages
    assert "💬 Комментарии — 17, обычно меньше одного" in message
    assert "комментарии 40" in message
