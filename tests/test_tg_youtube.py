"""Reading the Telegram seeds' YouTube links, and ranking what they name.

Pure functions over payloads and plain mappings: no database, no
network. Every post is synthetic — `tests/fixtures/tg_youtube_posts.json`
or built here — and every id, handle and name is invented.
"""

import json
import re
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

import pytest
from ytfakes import channel_id, video_id

from itgraph.db.models import YtLookupKind
from itgraph.derive.tg_youtube import (
    PREFILTER,
    Known,
    Name,
    NameKind,
    Post,
    PostLinks,
    Ranked,
    Reading,
    is_own,
    name_channels,
    post_urls,
    ranking,
    read_post,
    report,
    to_look_up,
)

POSTS: dict[str, Any] = json.loads(
    (Path(__file__).parent / "fixtures" / "tg_youtube_posts.json").read_text(
        encoding="utf-8"
    )
)

NOW = datetime(2026, 11, 1, 12, 0, tzinfo=UTC)


def _post(text: str = "", **fields: Any) -> dict[str, Any]:
    """A minimal payload: text, entities and media as given."""
    return {"message": text, "entities": [], "media": None, **fields}


def _url_entity(text: str, url: str) -> dict[str, Any]:
    start = text.index(url)
    return {
        "_": "MessageEntityUrl",
        "offset": len(text[:start].encode("utf-16-le")) // 2,
        "length": len(url.encode("utf-16-le")) // 2,
    }


# --- where a post's links are read -----------------------------------


def test_a_link_after_an_emoji_is_read_whole() -> None:
    links = read_post(POSTS["emoji_url"])
    assert links.handles == {"fake-seed-one"}


def test_a_link_behind_text() -> None:
    assert read_post(POSTS["text_url"]).video_ids == {"fakevid0001"}


def test_a_link_only_in_the_preview() -> None:
    assert read_post(POSTS["preview_only"]).video_ids == {"fakevid0002"}


def test_the_preview_s_metadata_is_not_read() -> None:
    assert read_post(POSTS["preview_metadata"]) == PostLinks()


def test_a_preview_without_a_url_contributes_nothing() -> None:
    assert post_urls(POSTS["preview_pending"]) == []


def test_a_link_in_text_and_preview_is_one_link() -> None:
    assert read_post(POSTS["preview_duplicate"]).video_ids == {"fakevid0003"}


def test_plain_text_telegram_did_not_mark_is_not_read() -> None:
    assert read_post(POSTS["plain_text_link"]) == PostLinks()


# --- link forms ------------------------------------------------------


def test_every_video_form_links_one_video() -> None:
    assert read_post(POSTS["video_forms"]).video_ids == {"fakevid0004"}


@pytest.mark.parametrize(
    "url",
    [
        "https://www.youtube.com/watch?v=fakevid0004",
        "youtu.be/fakevid0004?si=fake-share-token",
        "youtube.com/shorts/fakevid0004",
        "youtube.com/live/fakevid0004",
        "music.youtube.com/watch?v=fakevid0004",
    ],
)
def test_each_video_form_alone(url: str) -> None:
    text = f"link: {url}"
    links = read_post(_post(text, entities=[_url_entity(text, url)]))
    assert links.video_ids == {"fakevid0004"}


def test_a_percent_encoded_handle_in_another_script() -> None:
    assert read_post(POSTS["cyrillic_handle"]).handles == {"фейк-канал"}


def test_a_host_that_only_ends_like_youtube_links_nothing() -> None:
    assert read_post(POSTS["look_alike_hosts"]) == PostLinks()


def test_a_separator_ending_a_handle() -> None:
    assert read_post(POSTS["trailing_separator"]).handles == {"fake-seed"}


def test_a_channel_id_and_a_legacy_name() -> None:
    links = read_post(POSTS["channel_and_legacy"])
    assert links.channel_ids == {channel_id(7)}
    assert links.legacy_names == {"fakelegacy"}


# --- telegram mentions are not handles -------------------------------


def test_a_telegram_mention_is_not_a_handle() -> None:
    assert read_post(POSTS["mentions"]) == PostLinks()


# --- forms counted, never followed -----------------------------------


def test_custom_urls_playlists_and_shorteners_are_counted() -> None:
    links = read_post(POSTS["counted_forms"])
    assert links.custom_urls == {"fakecustom", "fakevanity"}
    assert links.playlists == 1
    assert links.shortened == 1
    assert not (links.channel_ids or links.handles or links.legacy_names)
    # `t.me/iv?url=…youtu.be…` is Telegram's page, not a YouTube link.
    assert links.video_ids == frozenset()


def test_one_playlist_written_twice_is_one() -> None:
    first = "youtube.com/playlist?list=PLfake-playlist-0001"
    second = "https://www.youtube.com/playlist?list=PLfake-playlist-0001"
    text = f"{first} и {second}"
    links = read_post(
        _post(
            text,
            entities=[_url_entity(text, first), _url_entity(text, second)],
        )
    )
    assert links.playlists == 1


# --- sources ---------------------------------------------------------


def test_a_forwarded_post_is_not_own() -> None:
    assert not is_own(POSTS["forwarded"])
    assert is_own(POSTS["emoji_url"])


def test_a_forward_from_a_user_is_not_own_either() -> None:
    payload = _post(
        "x",
        fwd_from={"_": "MessageFwdHeader", "from_name": "Fake Person"},
    )
    assert not is_own(payload)


# --- the loader's filter ---------------------------------------------


def test_the_prefilter_admits_every_post_that_links_anything() -> None:
    """The SQL filter is a superset of the reader: nothing is lost to it."""
    pattern = re.compile(PREFILTER, re.IGNORECASE)
    for key, payload in POSTS.items():
        if key == "_about" or read_post(payload) == PostLinks():
            continue
        assert any(pattern.search(url) for url in post_urls(payload)), key


def test_name_kinds_match_the_lookup_table() -> None:
    assert {kind.value for kind in NameKind} == {
        kind.value for kind in YtLookupKind
    }


# --- naming channels -------------------------------------------------

TARGET = channel_id(50)
OTHER = channel_id(51)


def _linking(
    family: int,
    *,
    channel: int | None = None,
    days_ago: int = 1,
    **links: Any,
) -> Post:
    """One own post of a seed in ``family``, linking what ``links`` says."""
    return Post(
        channel=channel if channel is not None else family,
        family=family,
        posted_at=NOW - timedelta(days=days_ago),
        links=PostLinks(
            **{key: frozenset(value) for key, value in links.items()}
        ),
    )


def test_two_affiliated_seeds_are_one_family() -> None:
    posts = [
        _linking(1, channel=1, channel_ids=[TARGET]),
        _linking(1, channel=2, channel_ids=[TARGET]),
    ]
    reading = name_channels(posts, Known())
    assert reading.channels[TARGET].families == {1}


def test_a_family_linking_repeatedly_is_one_family() -> None:
    known = Known(
        handles={"fake-target": frozenset({TARGET})},
        videos={video_id(n): TARGET for n in range(1, 5)},
    )
    posts = [
        _linking(1, handles=["fake-target"]),
        *(_linking(1, video_ids=[video_id(n)]) for n in range(1, 5)),
    ]
    reading = name_channels(posts, known)
    assert reading.channels[TARGET].families == {1}


def test_unaffiliated_seeds_are_separate_families() -> None:
    posts = [
        _linking(1, channel_ids=[TARGET]),
        _linking(2, channel_ids=[TARGET]),
    ]
    reading = name_channels(posts, Known())
    assert reading.channels[TARGET].families == {1, 2}


def test_a_video_link_names_its_channel_through_its_lookup() -> None:
    known = Known(lookups={Name(NameKind.VIDEO, video_id(1)): TARGET})
    reading = name_channels([_linking(1, video_ids=[video_id(1)])], known)
    assert reading.channels[TARGET].families == {1}


def test_two_forms_one_channel() -> None:
    known = Known(
        lookups={
            Name(NameKind.HANDLE, "fake-unknown"): TARGET,
            Name(NameKind.VIDEO, video_id(1)): TARGET,
        }
    )
    posts = [
        _linking(1, handles=["fake-unknown"]),
        _linking(2, video_ids=[video_id(1)]),
    ]
    reading = name_channels(posts, known)
    assert reading.channels[TARGET].families == {1, 2}
    assert reading.channels[TARGET].written == "@fake-unknown"


def test_a_link_not_yet_looked_up_awaits_one() -> None:
    reading = name_channels([_linking(1, video_ids=[video_id(1)])], Known())
    assert reading.awaiting == {Name(NameKind.VIDEO, video_id(1)): {1}}
    assert reading.channels == {}


def test_a_link_looked_up_as_not_found_names_nothing() -> None:
    known = Known(lookups={Name(NameKind.HANDLE, "fake-gone"): None})
    reading = name_channels([_linking(1, handles=["fake-gone"])], known)
    assert reading.not_found == {Name(NameKind.HANDLE, "fake-gone"): {1}}
    assert reading.channels == {}


def test_a_stored_video_without_a_channel_awaits_a_lookup() -> None:
    known = Known(videos={video_id(1): None})
    reading = name_channels([_linking(1, video_ids=[video_id(1)])], known)
    assert Name(NameKind.VIDEO, video_id(1)) in reading.awaiting


def test_a_handle_two_records_carry_awaits_a_lookup() -> None:
    known = Known(handles={"fake-dup": frozenset({TARGET, OTHER})})
    reading = name_channels([_linking(1, handles=["fake-dup"])], known)
    assert Name(NameKind.HANDLE, "fake-dup") in reading.awaiting


def test_a_handle_one_record_carries_needs_no_lookup() -> None:
    known = Known(handles={"fake-seed": frozenset({TARGET})})
    reading = name_channels([_linking(1, handles=["fake-seed"])], known)
    assert reading.awaiting == {}
    assert reading.channels[TARGET].families == {1}


def test_the_newest_link_is_kept() -> None:
    posts = [
        _linking(1, channel_ids=[TARGET], days_ago=30),
        _linking(2, channel_ids=[TARGET], days_ago=2),
    ]
    reading = name_channels(posts, Known())
    assert reading.channels[TARGET].newest == NOW - timedelta(days=2)


# --- ranking ---------------------------------------------------------


def _reading(spec: dict[str, list[tuple[int, int]]]) -> Reading:
    """Channels, each with (family, days ago) of the posts linking it."""
    posts = [
        _linking(family, channel_ids=[channel], days_ago=days)
        for channel, links in spec.items()
        for family, days in links
    ]
    return name_channels(posts, Known())


def test_more_families_rank_first() -> None:
    reading = _reading(
        {TARGET: [(1, 5), (2, 5)], OTHER: [(1, 5), (2, 5), (3, 5)]}
    )
    rows = ranking(reading, frozenset())
    assert [row.channel_id for row in rows] == [OTHER, TARGET]


def test_a_tie_goes_to_the_newer_link() -> None:
    reading = _reading(
        {TARGET: [(1, 400), (2, 365)], OTHER: [(1, 400), (2, 7)]}
    )
    rows = ranking(reading, frozenset())
    assert [row.channel_id for row in rows] == [OTHER, TARGET]


def test_known_channels_are_never_ranked() -> None:
    known = [channel_id(n) for n in range(60, 65)]
    reading = _reading(
        {channel: [(f, 1) for f in range(1, 6)] for channel in known}
    )
    assert ranking(reading, frozenset(known)) == []


def test_one_family_is_not_enough_by_default() -> None:
    reading = _reading({TARGET: [(1, 1)]})
    assert ranking(reading, frozenset()) == []
    assert len(ranking(reading, frozenset(), min_families=1)) == 1


def test_ties_are_ordered_the_same_way_every_time() -> None:
    spec = {TARGET: [(1, 3), (2, 3)], OTHER: [(1, 3), (2, 3)]}
    first = ranking(_reading(spec), frozenset())
    second = ranking(_reading(dict(reversed(spec.items()))), frozenset())
    assert first == second
    assert [row.channel_id for row in first] == sorted([TARGET, OTHER])


def test_names_are_looked_up_best_evidenced_first() -> None:
    posts = [
        _linking(1, handles=["fake-one"]),
        *(_linking(f, handles=["fake-three"]) for f in (1, 2, 3)),
    ]
    reading = name_channels(posts, Known())
    assert to_look_up(reading) == [
        Name(NameKind.HANDLE, "fake-three"),
        Name(NameKind.HANDLE, "fake-one"),
    ]


# --- the report ------------------------------------------------------


def test_a_ranked_row() -> None:
    posts = [
        _linking(1, handles=["fake-unknown"], days_ago=3),
        _linking(2, video_ids=[video_id(1)], days_ago=10),
    ]
    known = Known(
        lookups={
            Name(NameKind.HANDLE, "fake-unknown"): TARGET,
            Name(NameKind.VIDEO, video_id(1)): TARGET,
        }
    )
    reading = name_channels(posts, known)
    rows = ranking(reading, frozenset())
    lines = report(reading, rows, top=100, inventory=frozenset())

    row = next(line for line in lines if TARGET in line)
    assert "@fake-unknown" in row
    assert "2 families" in row
    assert (NOW - timedelta(days=3)).date().isoformat() in row


def test_the_top_and_the_rest() -> None:
    rows = [
        Ranked(channel_id(n), 3 if n < 20 else 2, NOW, None)
        for n in range(150)
    ]
    lines = report(Reading(), rows, top=100, inventory=frozenset())

    shown = [line for line in lines if "UCfake_channel_" in line]
    assert len(shown) == 100
    assert "  … and 50 more: 50 with 2 families" in lines


def test_what_could_not_be_counted() -> None:
    known = Known(lookups={Name(NameKind.HANDLE, "fake-gone"): None})
    posts = [
        _linking(1, video_ids=[video_id(1)], handles=["fake-gone"]),
        Post(
            channel=2,
            family=2,
            posted_at=NOW,
            links=PostLinks(
                custom_urls=frozenset({"fakecustom"}), playlists=1, shortened=2
            ),
        ),
    ]
    reading = name_channels(posts, known)
    lines = report(reading, [], top=100, inventory=frozenset())

    assert "awaiting a lookup: 1 links — run `itgraph yt tg-lookup`" in lines
    assert "looked up, no channel: 1 links" in lines
    assert any(
        line.strip().startswith("fakecustom") and "1 family" in line
        for line in lines
    )
    assert "counted: 1 playlist pages, 2 shortened links" in lines


def test_the_report_counts_channels_already_known() -> None:
    reading = _reading({TARGET: [(1, 1), (2, 1)]})
    lines = report(reading, [], top=100, inventory=frozenset({TARGET}))
    assert "linked channels already in the inventory: 1" in lines
