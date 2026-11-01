"""Reading what a YouTube video references, one form at a time.

Pure functions over text and plain mappings: no database, no network.
Every text is synthetic — `tests/fixtures/yt_texts.json` or written
here — and every id and handle is invented.
"""

import json
import unicodedata
from datetime import UTC, datetime
from pathlib import Path

import pytest
from ytfakes import channel_id, video_id

from itgraph.derive.yt_references import (
    Edge,
    Form,
    Known,
    NotFollowed,
    Pending,
    PendingKind,
    Source,
    normalize_handle,
    ranked,
    read_text,
    resolve,
)

TEXTS = json.loads(
    (Path(__file__).parent / "fixtures" / "yt_texts.json").read_text(
        encoding="utf-8"
    )
)

SEED_ONE = channel_id(1)
SEED_TWO = channel_id(2)
SEED_THREE = channel_id(3)
REJECTED = channel_id(4)
PUBLISHED = datetime(2026, 9, 1, tzinfo=UTC)


def known(
    *,
    handles: dict[str, set[str]] | None = None,
    channels: set[str] | None = None,
    videos: dict[str, str | None] | None = None,
) -> Known:
    return Known(
        handles={h: frozenset(ids) for h, ids in (handles or {}).items()},
        channels=frozenset(channels or set()),
        videos=videos or {},
    )


INVENTORY = known(
    handles={
        "fake-seed-one": {SEED_ONE},
        "fake.seed.two": {SEED_TWO},
        "fake-seed-three": {SEED_THREE},
        "fake-rejected": {REJECTED},
    },
    channels={SEED_ONE, SEED_TWO, SEED_THREE, REJECTED},
    videos={video_id(1): SEED_ONE, video_id(2): SEED_TWO},
)


def source(text: str, *, channel: str = SEED_ONE, video: int = 100) -> Source:
    return Source(
        video_id=video_id(video),
        channel_id=channel,
        published_at=PUBLISHED,
        named=read_text(text),
    )


def edges_of(text: str, *, channel: str = SEED_ONE) -> list[Edge]:
    return resolve([source(text, channel=channel)], INVENTORY).edges


# --- channel links ---------------------------------------------------


def test_a_handle_link_with_a_trailing_path_and_a_query() -> None:
    named = read_text(
        "https://www.youtube.com/@fake-seed/videos?sub_confirmation=1"
    )
    assert named.handles == ("fake-seed",)


def test_a_channel_id_link_on_another_host() -> None:
    named = read_text(f"music.youtube.com/channel/{SEED_TWO}")
    assert named.channel_ids == (SEED_TWO,)


def test_a_legacy_name_link() -> None:
    assert read_text("youtube.com/user/FakeLegacy").legacy_names == (
        "fakelegacy",
    )


def test_a_link_ending_a_sentence() -> None:
    named = read_text("see youtube.com/@fake.seed.two.")
    assert named.handles == ("fake.seed.two",)


def test_a_percent_encoded_handle_is_decoded() -> None:
    named = read_text("youtube.com/@%D1%84%D0%B5%D0%B9%D0%BA")
    assert named.handles == ("фейк",)


def test_the_fixture_links_in_every_host_and_shape() -> None:
    named = read_text(TEXTS["channel_links"])
    assert named.handles == ("fake-seed-one", "fake.seed.two")
    assert named.channel_ids == (SEED_TWO,)
    assert named.legacy_names == ("fakelegacy",)
    assert named.not_followed == {
        NotFollowed.CUSTOM_URL: 2,
        NotFollowed.PLAYLIST: 1,
    }


@pytest.mark.parametrize(
    "link",
    [
        "youtube.com/@fake-seed",
        "http://youtube.com/@fake-seed",
        "https://m.youtube.com/@fake-seed/featured",
        "WWW.YOUTUBE.COM/@fake-seed",
        "(youtube.com/@fake-seed)",
    ],
)
def test_a_handle_link_in_the_forms_it_gets_written_in(link: str) -> None:
    assert read_text(link).handles == ("fake-seed",)


def test_a_similar_host_is_no_link() -> None:
    named = read_text("notyoutube.com/@fake-seed-one")
    assert named.handles == ()
    assert named.mentions == ()


# --- handle grammar --------------------------------------------------


def test_letter_case_does_not_matter() -> None:
    assert normalize_handle("@Fake_Seed") == "fake_seed"
    edges = edges_of("youtube.com/@Fake-Seed-Three")
    assert [edge.target_channel for edge in edges] == [SEED_THREE]


def test_a_separator_cannot_end_a_handle() -> None:
    assert read_text("youtube.com/@fake-seed-").handles == ("fake-seed",)


def test_a_handle_too_short_to_exist() -> None:
    assert read_text("youtube.com/@ab").handles == ()


@pytest.mark.parametrize(
    "raw",
    ["ab", "_fake", "-fake", "a" * 31, "fake seed", "fake!seed"],
)
def test_what_cannot_be_a_handle(raw: str) -> None:
    assert normalize_handle(raw) is None


def test_thirty_characters_is_a_handle() -> None:
    assert normalize_handle("a" * 30) == "a" * 30


def test_handles_in_other_scripts() -> None:
    named = read_text(TEXTS["scripts"])
    assert named.handles == ("фейк", "偽物チャンネル", "नकली")
    assert named.mentions == ("фейк_канал",)


def test_a_decomposed_letter_is_the_composed_one() -> None:
    decomposed = unicodedata.normalize("NFD", "café-fake")
    assert decomposed != "café-fake"
    assert normalize_handle(decomposed) == "café-fake"


# --- video links -----------------------------------------------------


def test_a_video_inside_a_playlist() -> None:
    named = read_text(
        f"youtube.com/watch?v={video_id(2)}&list=PLfakefake&t=30"
    )
    assert named.video_ids == (video_id(2),)
    assert named.not_followed == {}


def test_a_short_link_with_a_share_token() -> None:
    named = read_text(f"youtu.be/{video_id(2)}?si=fake-share-token")
    assert named.video_ids == (video_id(2),)


def test_every_video_form() -> None:
    named = read_text(TEXTS["video_links"])
    assert named.video_ids == tuple(video_id(n) for n in range(2, 9))
    assert named.handles == named.channel_ids == ()


def test_a_video_id_of_the_wrong_length_is_nothing() -> None:
    assert read_text("youtu.be/fakevid02").video_ids == ()
    assert read_text("youtube.com/watch?v=fakevid000002").video_ids == ()


# --- forms counted, never followed -----------------------------------


def test_a_custom_url() -> None:
    named = read_text("youtube.com/c/FakeCustom")
    assert named.handles == named.legacy_names == ()
    assert named.not_followed == {NotFollowed.CUSTOM_URL: 1}


def test_youtube_s_own_pages_are_neither_followed_nor_counted() -> None:
    named = read_text(
        "youtube.com/results?search_query=fake youtube.com/hashtag/fake "
        "youtube.com/feed/subscriptions youtube.com/redirect?q=fake"
    )
    assert named == read_text("")


def test_a_shortened_link() -> None:
    named = read_text("bit.ly/fake-short")
    assert named.not_followed == {NotFollowed.SHORTENED: 1}


def test_a_telegram_link_is_counted_and_its_name_kept_nowhere() -> None:
    named = read_text("t.me/fake_tg_channel")
    assert named.not_followed == {NotFollowed.TELEGRAM: 1}
    references = resolve([source("t.me/fake_tg_channel")], INVENTORY)
    assert "fake_tg_channel" not in repr(named)
    assert "fake_tg_channel" not in repr(references)


def test_the_fixture_traps() -> None:
    named = read_text(TEXTS["traps"])
    assert named.handles == named.channel_ids == named.video_ids == ()
    # @Override, @media and @fake_twitter_name are well-formed; the
    # inventory decides. @types and @fakescope precede a `/`, the mail
    # address and the similar host are no mention, @ab is too short.
    assert named.mentions == ("override", "media", "fake_twitter_name")
    assert named.not_followed == {
        NotFollowed.SHORTENED: 3,
        NotFollowed.TELEGRAM: 2,
    }


# --- bare mentions ---------------------------------------------------


def test_a_mention_of_a_known_channel_in_a_title() -> None:
    edges = edges_of("Guest @fake.seed.two on caching")
    assert [(e.target_channel, e.form) for e in edges] == [
        (SEED_TWO, Form.MENTION)
    ]


def test_a_mention_of_an_unknown_handle_is_counted_not_followed() -> None:
    references = resolve([source("Twitter: @fake_twitter_name")], INVENTORY)
    assert references.edges == []
    assert references.pending == {}
    assert references.not_followed == {NotFollowed.UNKNOWN_MENTION: 1}


def test_an_email_address_is_no_mention() -> None:
    assert read_text("fake@fake-seed-one.example").mentions == ()
    assert edges_of("write to fake@fake-seed-one.example") == []


def test_a_package_scope_is_no_mention() -> None:
    inventory = known(handles={"fakescope": {SEED_TWO}}, channels={SEED_TWO})
    named = read_text("npm i @fakescope/fake-package")
    assert named.mentions == ()
    assert resolve([source("@fakescope/fake-package")], inventory).edges == []


def test_a_mention_ending_a_sentence() -> None:
    assert read_text("Thanks @fake.seed.two.").mentions == ("fake.seed.two",)


def test_a_handle_link_is_not_read_again_as_a_mention() -> None:
    assert read_text("youtube.com/@fake.seed.two").mentions == ()


# --- known targets ---------------------------------------------------


def test_a_link_to_another_seed() -> None:
    [edge] = edges_of("youtube.com/@fake.seed.two")
    assert edge == Edge(
        video_id=video_id(100),
        channel_id=SEED_ONE,
        published_at=PUBLISHED,
        target_channel=SEED_TWO,
        target_video=None,
        form=Form.HANDLE_LINK,
    )


def test_a_link_to_a_stored_video() -> None:
    [edge] = edges_of(f"youtu.be/{video_id(2)}")
    assert (edge.target_channel, edge.target_video, edge.form) == (
        SEED_TWO,
        video_id(2),
        Form.VIDEO_LINK,
    )


def test_a_rejected_channel_is_a_target() -> None:
    [edge] = edges_of(f"youtube.com/channel/{REJECTED}")
    assert edge.target_channel == REJECTED


def test_a_channel_known_by_id_alone() -> None:
    gone = channel_id(5)
    inventory = known(channels={SEED_ONE, gone})
    references = resolve([source(f"youtube.com/channel/{gone}")], inventory)
    assert [e.target_channel for e in references.edges] == [gone]


# --- pending targets -------------------------------------------------


def test_an_unknown_handle_link_is_pending() -> None:
    references = resolve([source("youtube.com/@fake-unknown")], INVENTORY)
    assert references.edges == []
    evidence = references.pending[Pending(PendingKind.HANDLE, "fake-unknown")]
    assert evidence.seeds == {SEED_ONE}


def test_two_seeds_are_two_sources() -> None:
    unknown = channel_id(77)
    references = resolve(
        [
            source(f"youtube.com/channel/{unknown}", channel=SEED_ONE),
            source(f"youtube.com/channel/{unknown}", channel=SEED_TWO),
        ],
        INVENTORY,
    )
    evidence = references.pending[Pending(PendingKind.CHANNEL, unknown)]
    assert evidence.seeds == {SEED_ONE, SEED_TWO}


def test_one_seed_linking_repeatedly_is_one_source() -> None:
    unknown = video_id(99)
    references = resolve(
        [source(f"youtu.be/{unknown}", video=n) for n in range(5)],
        INVENTORY,
    )
    evidence = references.pending[Pending(PendingKind.VIDEO, unknown)]
    assert evidence.seeds == {SEED_ONE}
    assert len(evidence.videos) == 5


def test_a_legacy_name_is_pending() -> None:
    references = resolve([source("youtube.com/user/fakelegacy")], INVENTORY)
    assert Pending(PendingKind.LEGACY, "fakelegacy") in references.pending


def test_a_stored_video_without_a_channel_is_pending() -> None:
    inventory = known(videos={video_id(3): None})
    references = resolve([source(f"youtu.be/{video_id(3)}")], inventory)
    assert references.edges == []
    assert Pending(PendingKind.VIDEO, video_id(3)) in references.pending


# --- one reference per target per video ------------------------------


def test_a_channel_linked_and_mentioned_is_one_link() -> None:
    edges = edges_of("youtube.com/@fake.seed.two and @fake.seed.two")
    assert [(e.target_channel, e.form) for e in edges] == [
        (SEED_TWO, Form.HANDLE_LINK)
    ]


def test_an_id_link_comes_before_a_handle_link() -> None:
    edges = edges_of(
        f"@fake.seed.two youtube.com/@fake.seed.two "
        f"youtube.com/channel/{SEED_TWO}"
    )
    assert [e.form for e in edges] == [Form.ID_LINK]


def test_a_channel_and_one_of_its_videos_are_two() -> None:
    edges = edges_of(f"youtube.com/@fake.seed.two youtu.be/{video_id(2)}")
    assert {(e.target_channel, e.target_video) for e in edges} == {
        (SEED_TWO, None),
        (SEED_TWO, video_id(2)),
    }


def test_the_same_video_linked_twice_is_one() -> None:
    edges = edges_of(
        f"youtu.be/{video_id(2)} youtube.com/watch?v={video_id(2)}"
    )
    assert len(edges) == 1


# --- no self-references ----------------------------------------------


def test_a_subscribe_link_to_the_own_channel() -> None:
    assert edges_of(f"youtube.com/channel/{SEED_ONE}?sub_confirmation=1") == []


def test_the_own_handle() -> None:
    references = resolve([source("I am @fake-seed-one")], INVENTORY)
    assert references.edges == []
    assert references.not_followed == {}


def test_a_link_to_an_earlier_episode() -> None:
    assert edges_of(f"youtu.be/{video_id(1)}") == []


def test_a_self_reference_does_not_hide_the_others() -> None:
    edges = edges_of(
        f"youtube.com/@fake-seed-one youtube.com/channel/{SEED_THREE}"
    )
    assert [e.target_channel for e in edges] == [SEED_THREE]


# --- ambiguity -------------------------------------------------------


def test_an_ambiguous_handle_names_no_channel() -> None:
    inventory = known(
        handles={"fake-dup": {SEED_TWO, SEED_THREE}},
        channels={SEED_TWO, SEED_THREE},
    )
    references = resolve([source("youtube.com/@fake-dup")], inventory)
    assert references.edges == []
    assert references.pending == {}
    assert references.ambiguous == {"fake-dup": {SEED_TWO, SEED_THREE}}


def test_an_ambiguous_handle_the_video_s_channel_carries_is_its_own() -> None:
    inventory = known(
        handles={"fake-dup": {SEED_ONE, SEED_THREE}},
        channels={SEED_ONE, SEED_THREE},
    )
    references = resolve([source("@fake-dup")], inventory)
    assert references.edges == []
    assert references.ambiguous == {}


# --- what a reading adds up to ---------------------------------------


def test_counts_add_up_over_the_sources() -> None:
    references = resolve(
        [
            source("bit.ly/fake-one t.me/fake_one", channel=SEED_ONE),
            source("bit.ly/fake-two", channel=SEED_TWO, video=101),
            source("", channel=SEED_TWO, video=102),
        ],
        INVENTORY,
    )
    assert (references.videos, references.seeds) == (3, 2)
    assert references.not_followed == {
        NotFollowed.SHORTENED: 2,
        NotFollowed.TELEGRAM: 1,
    }


def test_known_targets_count_distinct_seeds() -> None:
    references = resolve(
        [
            source("@fake.seed.two", channel=SEED_ONE, video=1),
            source("@fake.seed.two", channel=SEED_ONE, video=2),
            source(f"youtu.be/{video_id(2)}", channel=SEED_THREE, video=3),
        ],
        INVENTORY,
    )
    assert references.known_targets() == {SEED_TWO: {SEED_ONE, SEED_THREE}}


# --- ordering --------------------------------------------------------


def test_the_most_named_target_first() -> None:
    rows = ranked(
        {
            "fake-one": frozenset({SEED_ONE}),
            "fake-two": frozenset({SEED_ONE, SEED_TWO}),
        },
        key=str,
    )
    assert [target for target, _ in rows] == ["fake-two", "fake-one"]


def test_ties_are_ordered_the_same_way_every_time() -> None:
    targets = {
        "fake-b": frozenset({SEED_ONE}),
        "fake-a": frozenset({SEED_TWO}),
    }
    first = ranked(targets, key=str)
    again = ranked(dict(reversed(list(targets.items()))), key=str)
    assert (
        [t for t, _ in first]
        == [t for t, _ in again]
        == [
            "fake-a",
            "fake-b",
        ]
    )


def test_bounded_by_evidence_and_by_rows() -> None:
    targets = {
        "fake-one": frozenset({SEED_ONE}),
        "fake-two": frozenset({SEED_ONE, SEED_TWO}),
        "fake-three": frozenset({SEED_ONE, SEED_TWO, SEED_THREE}),
    }
    assert [t for t, _ in ranked(targets, key=str, min_seeds=2)] == [
        "fake-three",
        "fake-two",
    ]
    assert len(ranked(targets, key=str, limit=1)) == 1
    assert len(ranked(targets, key=str, limit=0)) == 3


def test_a_row_carries_the_target_and_its_seeds_and_nothing_else() -> None:
    [row] = ranked({"fake-one": frozenset({SEED_ONE})}, key=str)
    assert row == ("fake-one", frozenset({SEED_ONE}))
