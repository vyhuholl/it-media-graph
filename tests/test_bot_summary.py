"""The day summary: the day a destination closes, what goes in it, and
when it is sent.

The wording and the day arithmetic are pure and are tested without a
database; the selection and the delivery are tested through the real
Telegram queue on the suite's schema, with a sender that remembers.
"""

import json
from datetime import UTC, datetime
from typing import Any
from zoneinfo import ZoneInfo

from sqlalchemy import text

from itgraph.bot.app import BotStats
from itgraph.bot.destinations import Destination, Platform
from itgraph.bot.queues import TelegramQueue
from itgraph.bot.render import (
    MESSAGE_LIMIT,
    summary_header,
    summary_parts,
    units,
)
from itgraph.bot.summary import day_window, maybe_summary
from itgraph.db.session import Database

MOSCOW = ZoneInfo("Europe/Moscow")
CHAT = 100000001
TOPIC = 901


def destination(
    *, quiet_from: int = 2, quiet_to: int = 7, zone: str = "Europe/Moscow"
) -> Destination:
    return Destination(
        key="telegram",
        platform=Platform.TELEGRAM,
        chat_id=CHAT,
        topic_id=TOPIC,
        setting="ALERT_CHAT_ID",
        daily_cap=20,
        quiet_from=quiet_from,
        quiet_to=quiet_to,
        digest_hour=9,
        zone=zone,
    )


class Recorder:
    """A sender that remembers, as `test_bot.py`'s does."""

    def __init__(self, *, fail_on: frozenset[int] = frozenset()) -> None:
        self.messages: list[tuple[str, Destination]] = []
        self.fail_on = fail_on
        self.calls = 0

    async def send(
        self, text: str, *, destination: Destination, **_: object
    ) -> None:
        self.calls += 1
        if self.calls in self.fail_on:
            raise RuntimeError("telegram is unreachable")
        self.messages.append((text, destination))


async def run_summary(
    database: Database,
    sender: Recorder,
    *,
    now: datetime,
    quiet_from: int = 2,
    quiet_to: int = 7,
) -> BotStats:
    stats = BotStats()
    await maybe_summary(
        database,
        TelegramQueue(),
        destination(quiet_from=quiet_from, quiet_to=quiet_to),
        sender,
        stats,
        now=now,
    )
    return stats


async def seed_alert(
    database: Database,
    *,
    channel: int,
    msg: int,
    username: str | None,
    delivered_at: datetime,
    raised_at: datetime | None = None,
    value: float = 3.7,
    held: bool = False,
) -> None:
    """One views spike delivered directly, as the delivery loop's own
    write leaves it; a held one as the digest's own write leaves it —
    held alerts are the digest's alone, and its delivery says ``digest``.
    The raw payload carries the post's ``date``, as a collected payload
    does: the summary's clock is read from it."""
    raised_at = raised_at or delivered_at
    async with database.session() as session:
        await session.execute(
            text(
                "INSERT INTO tg_channels (tg_id, username, title, "
                "discovered_via, status) VALUES (:c, :u, 'Example', "
                "'manual', 'seed') ON CONFLICT (tg_id) DO NOTHING"
            ),
            {"c": channel, "u": username},
        )
        payload: dict[str, Any] = {
            "_": "Message",
            "message": f"post {msg}",
            "date": delivered_at.isoformat(),
        }
        await session.execute(
            text(
                "INSERT INTO tg_raw_messages (channel_id, msg_id, payload) "
                "VALUES (:c, :m, CAST(:p AS jsonb)) ON CONFLICT DO NOTHING"
            ),
            {"c": channel, "m": msg, "p": json.dumps(payload)},
        )
        await session.execute(
            text(
                "INSERT INTO tg_alerts (kind, channel_id, msg_id, band, "
                "value, raised_at, held_at, delivered_at, delivery) VALUES "
                "('views_spike', :c, :m, 1, :v, :r, "
                "CASE WHEN :held THEN now() END, :d, CAST("
                "CASE WHEN :held THEN 'digest' ELSE 'direct' END "
                "AS tg_alert_delivery))"
            ),
            {
                "c": channel,
                "m": msg,
                "v": value,
                "r": raised_at,
                "held": held,
                "d": delivered_at,
            },
        )


async def markers(database: Database) -> list[tuple[str, str, bool]]:
    async with database.session() as session:
        rows = await session.execute(
            text(
                "SELECT destination, day::text, sent_at IS NOT NULL "
                "FROM bot_daily_summaries"
            )
        )
        return [(d, day, sent) for d, day, sent in rows]


# --- the day is the quiet window's complement -------------------------


def test_a_window_inside_the_day_runs_to_the_next_morning() -> None:
    """The 18th's day is 07:00 of the 18th to 02:00 of the 19th."""
    now = datetime(2026, 11, 19, 2, 5, tzinfo=MOSCOW)

    window = day_window(
        quiet_from=2, quiet_to=7, zone="Europe/Moscow", now=now
    )

    assert window is not None
    assert window.label.isoformat() == "2026-11-18"
    assert window.start == datetime(2026, 11, 18, 7, 0, tzinfo=MOSCOW)
    assert window.end == datetime(2026, 11, 19, 2, 0, tzinfo=MOSCOW)


def test_the_window_is_half_open() -> None:
    """The summary is due exactly at quiet_from: now at the end still
    names the day that ended."""
    now = datetime(2026, 11, 19, 2, 0, tzinfo=MOSCOW)

    window = day_window(
        quiet_from=2, quiet_to=7, zone="Europe/Moscow", now=now
    )

    assert window is not None
    assert window.label.isoformat() == "2026-11-18"
    assert window.end == now


def test_a_midnight_crossing_window_labels_the_date_it_starts_on() -> None:
    """Quiet hours 22:00–06:00 make a day of 06:00 to 22:00, both of the
    same date: the day ends on the date its window started."""
    now = datetime(2026, 11, 18, 22, 3, tzinfo=MOSCOW)

    window = day_window(
        quiet_from=22, quiet_to=6, zone="Europe/Moscow", now=now
    )

    assert window is not None
    assert window.label.isoformat() == "2026-11-18"
    assert window.start == datetime(2026, 11, 18, 6, 0, tzinfo=MOSCOW)
    assert window.end == datetime(2026, 11, 18, 22, 0, tzinfo=MOSCOW)


def test_a_zone_boundary_moves_the_day_with_the_zone() -> None:
    """One moment, two zones, two days: 02:00 Moscow on the 19th is
    23:00 UTC on the 18th — Moscow's day of the 18th has just ended,
    while in UTC the day that ended most recently is the 17th's."""
    moment = datetime(2026, 11, 18, 23, 0, tzinfo=UTC)

    moscow = day_window(
        quiet_from=2, quiet_to=7, zone="Europe/Moscow", now=moment
    )
    utc = day_window(quiet_from=2, quiet_to=7, zone="UTC", now=moment)

    assert moscow is not None and utc is not None
    assert moscow.label.isoformat() == "2026-11-18"
    assert utc.label.isoformat() == "2026-11-17"


def test_equal_bounds_define_no_day() -> None:
    """Quiet hours switched off: no day, no summary, ever."""
    assert (
        day_window(
            quiet_from=2,
            quiet_to=2,
            zone="Europe/Moscow",
            now=datetime.now(UTC),
        )
        is None
    )


# --- the wording ------------------------------------------------------


def test_the_header_names_the_day_without_a_count() -> None:
    header = summary_header(datetime(2026, 11, 18, tzinfo=UTC).date())

    assert header == "<b>Сводка за 18.11.2026:</b>"
    assert "оповещени" not in header


def test_a_continued_header_says_so() -> None:
    header = summary_header(
        datetime(2026, 11, 18, tzinfo=UTC).date(), continued=True
    )

    assert header == "<b>Сводка за 18.11.2026 — продолжение:</b>"


def test_a_day_longer_than_a_message_goes_in_parts_whole() -> None:
    entries = [
        f"👁 Просмотры — заметно выше обычного (z 3.7)\n"
        f"Канал {number} · 08:56\nhttps://t.me/example/{number}"
        for number in range(200)
    ]

    parts = summary_parts(
        entries, day=datetime(2026, 11, 18, tzinfo=UTC).date()
    )

    assert len(parts) > 1
    for part in parts:
        assert units(part.text) <= MESSAGE_LIMIT
    # Every entry whole in exactly one part.
    for entry in entries:
        assert sum(entry in part.text for part in parts) == 1


# --- the delivery -----------------------------------------------------


async def test_the_first_day_is_summarized_and_closed(
    database: Database,
) -> None:
    """The whole path, proven once: the day's direct alerts go out under
    the dated header, the marker closes, one message carries them all."""
    await seed_alert(
        database,
        channel=1,
        msg=1,
        username="example",
        delivered_at=datetime(2026, 11, 18, 8, 0, tzinfo=MOSCOW),
    )
    await seed_alert(
        database,
        channel=2,
        msg=2,
        username="other",
        delivered_at=datetime(2026, 11, 18, 22, 30, tzinfo=MOSCOW),
    )
    sender = Recorder()

    stats = await run_summary(
        database, sender, now=datetime(2026, 11, 19, 2, 0, tzinfo=MOSCOW)
    )

    (text_, dest) = sender.messages[0]
    assert dest.chat_id == CHAT
    assert dest.topic_id == TOPIC
    assert "<b>Сводка за 18.11.2026:</b>" in text_
    assert "https://t.me/example/1" in text_
    assert "https://t.me/other/2" in text_
    # Ordered by publication time, the day re-read as a timeline.
    assert text_.index("t.me/example/1") < text_.index("t.me/other/2")
    assert len(sender.messages) == 1
    assert stats.summaries == 1
    assert await markers(database) == [("telegram", "2026-11-18", True)]


async def test_the_entry_is_the_alerts_own_words_and_the_clock(
    database: Database,
) -> None:
    await seed_alert(
        database,
        channel=1,
        msg=1,
        username="example",
        delivered_at=datetime(2026, 11, 18, 8, 56, tzinfo=MOSCOW),
    )
    sender = Recorder()

    await run_summary(
        database, sender, now=datetime(2026, 11, 19, 2, 0, tzinfo=MOSCOW)
    )

    (text_, _) = sender.messages[0]
    assert "👁 Просмотры — заметно выше обычного (z 3.7)" in text_
    assert "Example · 08:56" in text_
    # No excerpt of the post: the entry is a record, not a copy.
    assert "post 1" not in text_


async def test_a_quiet_day_sends_nothing_and_leaves_no_marker(
    database: Database,
) -> None:
    sender = Recorder()

    stats = await run_summary(
        database, sender, now=datetime(2026, 11, 19, 2, 0, tzinfo=MOSCOW)
    )

    assert sender.messages == []
    assert stats.summaries == 0
    assert await markers(database) == []


async def test_held_and_out_of_window_deliveries_are_not_in_it(
    database: Database,
) -> None:
    """Held for the digest is the digest's alone; delivered before the
    window began is the previous day's."""
    await seed_alert(
        database,
        channel=1,
        msg=1,
        username="example",
        delivered_at=datetime(2026, 11, 18, 8, 0, tzinfo=MOSCOW),
        held=True,
    )
    await seed_alert(
        database,
        channel=2,
        msg=2,
        username="other",
        delivered_at=datetime(2026, 11, 18, 6, 30, tzinfo=MOSCOW),
    )
    sender = Recorder()

    await run_summary(
        database, sender, now=datetime(2026, 11, 19, 2, 0, tzinfo=MOSCOW)
    )

    assert sender.messages == []


async def test_a_restart_does_not_resend_a_sent_day(
    database: Database,
) -> None:
    await seed_alert(
        database,
        channel=1,
        msg=1,
        username="example",
        delivered_at=datetime(2026, 11, 18, 8, 0, tzinfo=MOSCOW),
    )
    sender = Recorder()
    await run_summary(
        database, sender, now=datetime(2026, 11, 19, 2, 0, tzinfo=MOSCOW)
    )
    assert len(sender.messages) == 1

    # The bot back at 14:00, the day already closed.
    restarted = Recorder()
    stats = await run_summary(
        database, restarted, now=datetime(2026, 11, 19, 14, 0, tzinfo=MOSCOW)
    )

    assert restarted.messages == []
    assert stats.summaries == 0


async def test_a_bot_down_at_the_start_sends_the_day_late(
    database: Database,
) -> None:
    await seed_alert(
        database,
        channel=1,
        msg=1,
        username="example",
        delivered_at=datetime(2026, 11, 18, 8, 0, tzinfo=MOSCOW),
    )
    sender = Recorder()

    stats = await run_summary(
        database, sender, now=datetime(2026, 11, 19, 9, 0, tzinfo=MOSCOW)
    )

    assert len(sender.messages) == 1
    assert stats.summaries == 1


async def test_a_failed_send_leaves_the_marker_open_and_resends_whole(
    database: Database,
) -> None:
    """At-least-once, as every send here: the failed part leaves the
    day open, and the next pass resends the whole summary."""
    await seed_alert(
        database,
        channel=1,
        msg=1,
        username="example",
        delivered_at=datetime(2026, 11, 18, 8, 0, tzinfo=MOSCOW),
    )
    sender = Recorder(fail_on=frozenset({1}))

    await run_summary(
        database, sender, now=datetime(2026, 11, 19, 2, 0, tzinfo=MOSCOW)
    )

    assert sender.messages == []
    assert await markers(database) == [("telegram", "2026-11-18", False)]

    retry = Recorder()
    await run_summary(
        database, retry, now=datetime(2026, 11, 19, 2, 1, tzinfo=MOSCOW)
    )

    assert len(retry.messages) == 1
    assert await markers(database) == [("telegram", "2026-11-18", True)]


async def test_equal_bounds_say_nothing_whatever_was_delivered(
    database: Database,
) -> None:
    """Quiet hours switched off define no day, so nothing closes it."""
    await seed_alert(
        database,
        channel=1,
        msg=1,
        username="example",
        delivered_at=datetime(2026, 11, 18, 8, 0, tzinfo=MOSCOW),
    )
    sender = Recorder()

    stats = await run_summary(
        database,
        sender,
        now=datetime(2026, 11, 19, 2, 0, tzinfo=MOSCOW),
        quiet_from=2,
        quiet_to=2,
    )

    assert sender.messages == []
    assert stats.summaries == 0


async def test_the_cap_and_the_alerts_are_untouched(
    database: Database,
) -> None:
    """The summary sends already-delivered rows and writes nothing of
    the queue: its marker is the only row it makes."""
    from itgraph.db.alerts import count_delivered_since
    from itgraph.db.models import AlertDelivery

    await seed_alert(
        database,
        channel=1,
        msg=1,
        username="example",
        delivered_at=datetime(2026, 11, 18, 8, 0, tzinfo=MOSCOW),
    )
    sender = Recorder()
    await run_summary(
        database, sender, now=datetime(2026, 11, 19, 2, 0, tzinfo=MOSCOW)
    )

    async with database.session() as session:
        sent = await count_delivered_since(
            session,
            since=datetime(2026, 11, 18, tzinfo=UTC),
            delivery=AlertDelivery.DIRECT,
        )
    assert sent == 1  # the alert's own delivery, not a new one


async def test_youtube_entries_go_per_route_with_the_video_named(
    database: Database,
) -> None:
    """YouTube's own queue, its own route and its own summary shape:
    the signal, then the video and its channel with the clock time."""
    from itgraph.bot.queues import YoutubeQueue
    from itgraph.db.models import YtAlertRoute, YtLang

    async with database.session() as session:
        await session.execute(
            text(
                "INSERT INTO yt_channels (channel_id, status, kind, lang, "
                "discovered_via) VALUES ('UCfakeChannel00000000001', "
                "'seed', 'personal', :lang, 'manual') "
                "ON CONFLICT (channel_id) DO NOTHING"
            ),
            {"lang": YtLang.EN.value},
        )
        await session.execute(
            text(
                "INSERT INTO yt_raw_channels (channel_id, payload, "
                "fetched_at) VALUES ('UCfakeChannel00000000001', "
                "CAST(:cp AS jsonb), now()) "
                "ON CONFLICT (channel_id) DO NOTHING"
            ),
            {
                "cp": json.dumps(
                    {
                        "snippet": {
                            "title": "Fake Channel",
                        }
                    }
                )
            },
        )
        await session.execute(
            text(
                "INSERT INTO yt_raw_videos (video_id, payload, fetched_at, "
                "discovered_at, discovered_via) VALUES ('fakeVid0001', "
                "CAST(:p AS jsonb), now(), now(), 'count') "
                "ON CONFLICT DO NOTHING"
            ),
            {
                "p": json.dumps(
                    {
                        "snippet": {
                            "channelId": "UCfakeChannel00000000001",
                            "title": "A fake video",
                            "publishedAt": "2026-11-18T05:33:00Z",
                        }
                    }
                )
            },
        )
        # The video was published at 05:33, inside the quiet hours, so
        # its spike was scored and delivered at 07:10, the first moment
        # outside them: the alert's scored_at names the stats reading it
        # was scored on, and the delivery falls inside the day's window
        # (07:00–02:00 UTC), whose clock the entry shows as 05:33.
        await session.execute(
            text(
                "INSERT INTO yt_raw_video_stats (video_id, observed_at, views) "
                "VALUES ('fakeVid0001', :at, 9000)"
            ),
            {"at": datetime(2026, 11, 18, 7, 10, tzinfo=UTC)},
        )
        await session.execute(
            text(
                "INSERT INTO yt_alerts (kind, video_id, scored_at, band, "
                "value, raised_at, delivered_at, delivery, route) VALUES "
                "('views_spike', 'fakeVid0001', :at, 1, 4.2, :at, :at, "
                "'direct', 'en')"
            ),
            {"at": datetime(2026, 11, 18, 7, 10, tzinfo=UTC)},
        )

    stats = BotStats()
    yt_destination = Destination(
        key="youtube_en",
        platform=Platform.YOUTUBE,
        chat_id=CHAT,
        topic_id=11,
        setting="ALERT_CHAT_ID",
        daily_cap=10,
        quiet_from=2,
        quiet_to=7,
        digest_hour=9,
        zone="UTC",
        route=YtAlertRoute.EN,
    )
    sender = Recorder()
    await maybe_summary(
        database,
        YoutubeQueue(),
        yt_destination,
        sender,
        stats,
        now=datetime(2026, 11, 19, 2, 0, tzinfo=UTC),
    )

    (text_, dest) = sender.messages[0]
    assert dest.topic_id == 11
    assert "<b>Сводка за 18.11.2026:</b>" in text_
    assert "👁 Просмотры — намного выше обычного (z 4.2)" in text_
    assert "A fake video — Fake Channel · 05:33" in text_
    assert "https://www.youtube.com/watch?v=fakeVid0001" in text_
    assert stats.summaries == 1
