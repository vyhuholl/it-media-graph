"""Telegram's weekly list, as the messages a person reads. Pure."""

from dataclasses import replace
from datetime import UTC, date, datetime, timedelta
from typing import Any

import pytest

from itgraph.bot.render import (
    MESSAGE_LIMIT,
    TgWeeklyEntryView,
    message_parts,
    render_tg_weekly_entry,
    tg_weekly_empty,
    tg_weekly_header,
    units,
)
from itgraph.db.models import AlertKind

ZONE = "Europe/Moscow"
WEEK = date(2026, 11, 16)
# Monday 23 November, 10:00 and 10:05 in Moscow.
COMPUTED = datetime(2026, 11, 23, 7, 0, tzinfo=UTC)
NOW = COMPUTED + timedelta(minutes=5)

VIEW = TgWeeklyEntryView(
    channel_title="Fake <channel> & co",
    channel_username="fake_weekly_one",
    msg_id=1234,
    published_at=NOW - timedelta(days=3, hours=2),
    text="A fake first line\nand a second one",
    kind=AlertKind.VIEWS_SPIKE,
    z=4.24,
    observed=8_200.0,
    expected=2_000.0,
    others=2,
)


def view(**changes: Any) -> TgWeeklyEntryView:
    return replace(VIEW, **changes)


def entry(number: int = 1, **changes: Any) -> str:
    return render_tg_weekly_entry(number, view(**changes), now=NOW)


def header(*, continued: bool = False, posts_scored: int = 3912) -> str:
    return tg_weekly_header(
        week_start=WEEK,
        computed_at=COMPUTED,
        posts_scored=posts_scored,
        continued=continued,
        now=NOW,
        zone=ZONE,
    )


# --- the head ----------------------------------------------------------


def test_the_head_names_the_list_the_week_and_whose_score() -> None:
    text = header()

    assert text.splitlines()[0] == (
        "📋 Telegram — выше нормы за неделю 16–22 ноября"
    )
    assert "оценено 3 912 постов" in text
    assert "Оценка itgraph на 10:00 MSK — рассчитана itgraph." in text


def test_the_moment_of_another_day_is_dated_in_moscow_time() -> None:
    text = tg_weekly_header(
        week_start=WEEK,
        computed_at=COMPUTED,
        posts_scored=10,
        continued=False,
        now=NOW + timedelta(days=1),
        zone=ZONE,
    )

    assert "Оценка itgraph на 23.11 10:00 MSK" in text


def test_a_resumed_list_says_it_continues() -> None:
    assert header(continued=True) == (
        "📋 Telegram — продолжение списка за неделю 16–22 ноября"
    )


@pytest.mark.parametrize(
    ("count", "says"),
    [
        (1, "1 пост."),
        (2, "2 поста."),
        (11, "11 постов."),
        (21, "21 пост."),
        (104, "104 поста."),
        (3900, "3 900 постов."),
    ],
)
def test_the_count_of_posts_reads_in_russian(count: int, says: str) -> None:
    assert f"оценено {says}" in header(posts_scored=count)


# --- an entry ------------------------------------------------------------


def test_an_entry_shows_what_an_alert_shows() -> None:
    lines = entry(3).splitlines()

    assert lines[0] == (
        "3. <b>Fake &lt;channel&gt; &amp; co</b> (@fake_weekly_one) · "
        "опубликовано 3 дн назад"
    )
    assert lines[1] == "https://t.me/fake_weekly_one/1234"
    assert lines[2] == "A fake first line"
    assert lines[3] == (
        "👁 Просмотры — в 4,1 раза выше обычного для канала (z 4.2); "
        "ещё 2 поста канала выше порога"
    )
    assert len(lines) == 4


def test_a_channel_without_a_username_has_no_link() -> None:
    text = entry(channel_username=None)

    assert "https://" not in text
    assert text.splitlines()[0].startswith("1. <b>Fake &lt;channel&gt;")
    assert "(@" not in text
    assert "A fake first line" in text
    assert "(z 4.2)" in text


def test_a_channel_without_a_title_is_named_by_its_username() -> None:
    assert entry(channel_title=None).startswith("1. @fake_weekly_one · ")


def test_a_long_first_line_is_cut_at_120_and_marked() -> None:
    paragraph = "слово " * 80  # 480 characters, no line break
    lines = entry(text=paragraph + "\nsecond").splitlines()

    quoted = lines[2]
    assert quoted.endswith("…")
    assert len(quoted) <= 121
    assert quoted[:-1] == paragraph[:120].rstrip()


def test_the_first_line_collapses_spaces_and_is_escaped() -> None:
    text = entry(text="  <b>bold</b>   &   spaced\n\nmore")

    assert "&lt;b&gt;bold&lt;/b&gt; &amp; spaced" in text
    assert "more" not in text


@pytest.mark.parametrize("body", [None, "", "  \n  "])
def test_a_post_with_no_text_has_no_line(body: str | None) -> None:
    lines = entry(text=body).splitlines()

    assert len(lines) == 3
    assert lines[2].startswith("👁 Просмотры")


def test_a_comment_entry_states_the_count_and_the_usual() -> None:
    text = entry(
        kind=AlertKind.COMMENT_SPIKE, observed=37.0, expected=3.2, z=5.06
    )

    assert "💬 Комментарии — 37, обычно около 3 (z 5.1)" in text
    assert "раза" not in text


@pytest.mark.parametrize(
    ("kind", "label"),
    [
        (AlertKind.REACTION_SPIKE, "❤️ Реакции"),
        (AlertKind.FORWARD_SPIKE, "🔁 Пересылки"),
    ],
)
def test_other_signals_read_as_their_alerts(
    kind: AlertKind, label: str
) -> None:
    assert f"{label} — в 4,1 раза выше обычного" in entry(kind=kind)


def test_no_count_of_views_reactions_or_forwards_is_shown() -> None:
    text = entry()

    assert "8200" not in text and "8 200" not in text
    assert "2000" not in text and "2 000" not in text


def test_a_lone_post_says_nothing_of_others() -> None:
    assert "ещё" not in entry(others=0)


def test_one_other_post_reads_in_russian() -> None:
    assert "; ещё 1 пост канала выше порога" in entry(others=1)


# --- the list --------------------------------------------------------------


@pytest.mark.parametrize("scored", [3900, 0])
def test_an_empty_week_is_said(scored: int) -> None:
    text = tg_weekly_empty(header(posts_scored=scored))

    assert "16–22 ноября" in text
    assert f"оценено {scored:,} постов".replace(",", " ") in text
    assert text.endswith("Выше порога — ничего.")


def test_ten_long_entries_split_whole_and_within_the_limit() -> None:
    entries = [
        entry(number, channel_title="Очень длинное название канала " * 10)
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
