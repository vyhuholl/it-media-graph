"""A weekly list, as the messages a person reads. Pure: no bot, no database."""

from dataclasses import replace
from datetime import UTC, date, datetime, timedelta
from typing import Any

import pytest

from itgraph.bot.render import (
    MESSAGE_LIMIT,
    WeeklyEntryView,
    message_parts,
    render_weekly_entry,
    units,
    week_label,
    weekly_empty,
    weekly_header,
)
from itgraph.db.models import YtAlertRoute, YtMetric
from itgraph.derive.yt_videos import Counters

ZONE = "Europe/Moscow"
WEEK = date(2026, 11, 16)
# Monday 23 November, 10:00 and 10:05 in Moscow.
COMPUTED = datetime(2026, 11, 23, 7, 0, tzinfo=UTC)
NOW = COMPUTED + timedelta(minutes=5)


VIEW = WeeklyEntryView(
    video_id="fakevid0901",
    title="Fake <talk> & more",
    channel_title="Fake Channel 1",
    short=False,
    started_at=NOW - timedelta(days=6, hours=2),
    counters=Counters(views=12_345, likes=678, comments=90),
    counted_at=COMPUTED - timedelta(minutes=13),
    metric=YtMetric.VIEWS,
    z=4.24,
    observed=8_200,
    expected=2_000.0,
    others=2,
)


def view(**changes: Any) -> WeeklyEntryView:
    return replace(VIEW, **changes)


def header(*, continued: bool = False, videos_scored: int = 214) -> str:
    return weekly_header(
        route=YtAlertRoute.RU,
        week_start=WEEK,
        computed_at=COMPUTED,
        videos_scored=videos_scored,
        continued=continued,
        now=NOW,
        zone=ZONE,
    )


@pytest.mark.parametrize(
    ("week", "label"),
    [
        (date(2026, 11, 16), "16–22 ноября"),
        (date(2026, 11, 30), "30 ноября – 6 декабря"),
        (date(2026, 12, 28), "28 декабря – 3 января"),
    ],
)
def test_a_week_reads_as_a_reader_names_it(week: date, label: str) -> None:
    assert week_label(week) == label


def test_the_head_names_the_list_the_week_and_whose_score() -> None:
    text = header()

    assert "YouTube · RU" in text
    assert "16–22 ноября" in text
    assert "оценено 214 видео" in text
    assert "Оценка itgraph на 10:00 MSK" in text
    assert "рассчитана itgraph, не метрика YouTube" in text


def test_a_resumed_list_says_it_continues() -> None:
    text = header(continued=True)

    assert "продолжение списка за неделю 16–22 ноября" in text


def test_an_entry_names_its_source_and_dates_what_it_shows() -> None:
    text = render_weekly_entry(3, view(), now=NOW, zone=ZONE)

    assert text.startswith("3. <b>Fake &lt;talk&gt; &amp; more</b>")
    assert "Fake Channel 1" in text
    assert "опубликовано 6 дн назад" in text
    assert "https://www.youtube.com/watch?v=fakevid0901" in text
    assert (
        "YouTube на 09:47 MSK: просмотры 12 345 · лайки 678 · комментарии 90"
        in text
    )
    assert "📈 Оценка itgraph: 👁 Просмотры — в 4,1 раза выше обычного" in text
    assert "(z 4.2)" in text
    assert "ещё 2 видео канала выше нормы" in text
    assert "Shorts" not in text


def test_a_short_is_marked() -> None:
    assert " · Shorts" in render_weekly_entry(
        1, view(short=True), now=NOW, zone=ZONE
    )


def test_a_counter_youtube_did_not_report_is_left_out() -> None:
    text = render_weekly_entry(
        1,
        view(counters=Counters(views=12_345, likes=None, comments=90)),
        now=NOW,
        zone=ZONE,
    )

    assert "лайки" not in text
    assert "просмотры 12 345 · комментарии 90" in text


def test_a_comment_entry_states_the_count_and_the_usual() -> None:
    text = render_weekly_entry(
        1,
        view(metric=YtMetric.COMMENTS, observed=24, expected=0.9, z=6.1),
        now=NOW,
        zone=ZONE,
    )

    assert "💬 Комментарии — 24, обычно меньше одного (z 6.1)" in text


def test_a_lone_video_says_nothing_of_others() -> None:
    assert "ещё" not in render_weekly_entry(
        1, view(others=0), now=NOW, zone=ZONE
    )


@pytest.mark.parametrize(
    ("observed", "says"),
    [
        (4_000, "в 2 раза"),
        (10_000, "в 5 раз"),
        (5_000, "в 2,5 раза"),
        (44_000, "в 22 раза"),
        (24_000, "в 12 раз"),
    ],
)
def test_the_multiple_reads_in_russian(observed: int, says: str) -> None:
    text = render_weekly_entry(
        1, view(observed=observed, expected=2_000.0), now=NOW, zone=ZONE
    )

    assert f"Просмотры — {says} выше обычного" in text


def test_an_empty_week_is_said() -> None:
    text = weekly_empty(header(videos_scored=180))

    assert "16–22 ноября" in text
    assert "оценено 180 видео" in text
    assert "Выше нормы — ничего." in text


def test_ten_long_entries_split_whole_and_within_the_limit() -> None:
    entries = [
        render_weekly_entry(
            number,
            view(title="Очень длинное название видео " * 12),
            now=NOW,
            zone=ZONE,
        )
        for number in range(1, 11)
    ]

    parts = message_parts(
        entries, first=header(), later=header(continued=True)
    )

    assert len(parts) > 1
    assert all(units(part.text) <= MESSAGE_LIMIT for part in parts)
    assert [i for part in parts for i in part.entries] == list(range(10))
    assert parts[0].text.splitlines()[0].endswith(f"(1/{len(parts)})")
    assert "продолжение списка" in parts[1].text.splitlines()[0]
    for part in parts:
        for index in part.entries:
            assert entries[index] in part.text
