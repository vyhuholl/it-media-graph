"""The second wind section as a reader reads it, on both platforms."""

from datetime import UTC, datetime, timedelta

from itgraph.bot.render import (
    MESSAGE_LIMIT,
    SECOND_WIND_NONE,
    SECOND_WIND_POSTS,
    SECOND_WIND_VIDEOS,
    TgRevivalView,
    YtRevivalView,
    message_parts,
    render_tg_revival,
    render_yt_revival,
    units,
)

ZONE = "Europe/Moscow"
NOW = datetime(2026, 11, 23, 7, 5, tzinfo=UTC)


def tg_view(**changes: object) -> TgRevivalView:
    fields: dict[str, object] = {
        "channel_title": "Fake Channel",
        "channel_username": "fake_channel",
        "msg_id": 1234,
        "published_at": NOW - timedelta(days=18),
        "text": "A first line of the post\nand a second line",
        "acceleration": 4.0,
        "share": 2.8,
        "others": 0,
    }
    fields.update(changes)
    return TgRevivalView(**fields)  # type: ignore[arg-type]


def yt_view(**changes: object) -> YtRevivalView:
    fields: dict[str, object] = {
        "video_id": "fakeWind001",
        "title": "A Fake Talk",
        "channel_title": "Fake Channel",
        "started_at": NOW - timedelta(days=60),
        "start_views": 12345,
        "start_at": datetime(2026, 11, 16, 2, 2, tzinfo=UTC),
        "end_views": 15145,
        "end_at": datetime(2026, 11, 23, 2, 1, tzinfo=UTC),
        "acceleration": 8.0,
        "share": 1.4,
        "others": 0,
    }
    fields.update(changes)
    return YtRevivalView(**fields)  # type: ignore[arg-type]


def test_a_telegram_entry_shows_what_sped_up() -> None:
    text = render_tg_revival(1, tg_view(), now=NOW)

    assert text.startswith("1. <b>Fake Channel</b> (@fake_channel)")
    assert "опубликовано 18 дн назад" in text
    assert "https://t.me/fake_channel/1234" in text
    assert "A first line of the post" in text
    assert "and a second line" not in text
    assert "в 4 раза быстрее прошлой недели" in text
    assert "в 2,8 раза больше" in text
    assert "(оценка itgraph)" in text
    assert SECOND_WIND_POSTS not in text


def test_a_telegram_entry_shows_no_count() -> None:
    text = render_tg_revival(1, tg_view(), now=NOW)
    for word in ("просмотров:", "реакц", "репост"):
        assert word not in text


def test_a_telegram_entry_without_a_username_has_no_link() -> None:
    text = render_tg_revival(1, tg_view(channel_username=None), now=NOW)
    assert "t.me" not in text
    assert "Fake Channel" in text


def test_a_post_that_did_not_grow_the_week_before() -> None:
    text = render_tg_revival(1, tg_view(acceleration=None), now=NOW)
    assert "Пост снова начал набирать просмотры" in text
    assert "быстрее" not in text


def test_a_share_under_the_norm_is_a_percentage() -> None:
    text = render_tg_revival(1, tg_view(share=0.6), now=NOW)
    assert (
        "за неделю 60% того, что обычный новый пост канала за 8 часов" in text
    )


def test_the_others_of_a_channel() -> None:
    text = render_tg_revival(1, tg_view(others=2), now=NOW)
    assert "ещё 2 поста канала разогнались" in text


def test_the_heading_travels_with_the_first_entry() -> None:
    text = render_tg_revival(1, tg_view(), now=NOW, heading=True)
    assert text.startswith(SECOND_WIND_POSTS + "\n1. ")


def test_a_youtube_entry_names_its_source_and_dates_what_it_shows() -> None:
    text = render_yt_revival(1, yt_view(), now=NOW, zone=ZONE)

    assert text.startswith("1. <b>A Fake Talk</b> — Fake Channel")
    assert "опубликовано 60 дн назад" in text
    assert "https://www.youtube.com/watch?v=fakeWind001" in text
    assert "YouTube: 12 345 просмотров на 16.11 05:02 MSK" in text
    # The end of the week is today: its moment needs no date.
    assert "→ 15 145 на 05:01 MSK (+2 800)" in text
    assert "🔁 Оценка itgraph: в 8 раз быстрее прошлой недели" in text
    assert "в 1,4 раза больше, чем обычное новое видео канала за сутки" in text


def test_a_youtube_entry_without_counts_shows_none() -> None:
    text = render_yt_revival(1, yt_view(start_views=None), now=NOW, zone=ZONE)
    assert "YouTube:" not in text
    assert "Оценка itgraph" in text


def test_a_video_that_did_not_grow_the_week_before() -> None:
    text = render_yt_revival(
        1, yt_view(acceleration=None, others=1), now=NOW, zone=ZONE
    )
    assert "видео снова начало набирать просмотры" in text
    assert "ещё 1 видео канала разогнались" in text


def test_nothing_sped_up_is_one_line() -> None:
    assert "\n" not in SECOND_WIND_NONE
    assert "ничего не разогналось" in SECOND_WIND_NONE
    assert SECOND_WIND_VIDEOS.startswith("🔁 Второе дыхание")


def test_parts_keep_the_heading_with_the_first_revival() -> None:
    """A list long enough to split: the heading is never left at the end
    of one part with its first entry in the next."""
    filler = ["x" * 900 for _ in range(4)]
    revivals = [
        render_tg_revival(
            number, tg_view(text="y" * 500), now=NOW, heading=number == 1
        )
        for number in (1, 2)
    ]

    parts = message_parts(filler + revivals, first="head", later="more")

    holding = [part.text for part in parts if SECOND_WIND_POSTS in part.text]
    assert len(holding) == 1
    heading_at = holding[0].index(SECOND_WIND_POSTS)
    assert holding[0][heading_at:].split("\n")[1].startswith("1. ")
    assert all(units(part.text) <= MESSAGE_LIMIT for part in parts)
