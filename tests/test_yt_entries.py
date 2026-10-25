"""Operator entries → lookups, refused together before anything is spent."""

import pytest
from ytfakes import channel_id

from itgraph.yt_entries import (
    ChannelIdLookup,
    EntryError,
    HandleLookup,
    LegacyNameLookup,
    parse_entries,
    read_entries,
)

ID = channel_id(7)


@pytest.mark.parametrize(
    ("entry", "lookup"),
    [
        ("@FakeHandle", HandleLookup("fakehandle")),
        ("fakehandle", HandleLookup("fakehandle")),
        ("fake.handle_with-dots", HandleLookup("fake.handle_with-dots")),
        ("youtube.com/@fakehandle", HandleLookup("fakehandle")),
        ("https://www.youtube.com/@fakehandle", HandleLookup("fakehandle")),
        ("http://m.youtube.com/@fakehandle", HandleLookup("fakehandle")),
        (
            "https://www.youtube.com/@fakehandle/videos",
            HandleLookup("fakehandle"),
        ),
        (
            "https://www.youtube.com/@fakehandle?si=share",
            HandleLookup("fakehandle"),
        ),
        # A non-Latin handle as a browser copies it, percent-encoded.
        (
            "https://www.youtube.com/@%D1%84%D0%B5%D0%B9%D0%BA",
            HandleLookup("фейк"),
        ),
        (ID, ChannelIdLookup(ID)),
        (f"https://www.youtube.com/channel/{ID}", ChannelIdLookup(ID)),
        (f"youtube.com/channel/{ID}/about", ChannelIdLookup(ID)),
        (
            "https://www.youtube.com/user/FakeOldName",
            LegacyNameLookup("fakeoldname"),
        ),
        ("  @fakehandle  ", HandleLookup("fakehandle")),
    ],
)
def test_accepted_forms(entry: str, lookup: object) -> None:
    [parsed] = parse_entries([entry])
    assert parsed.lookup == lookup


@pytest.mark.parametrize(
    ("entry", "says"),
    [
        ("https://www.youtube.com/c/FakeCustom", "/c/ custom URL"),
        ("https://www.youtube.com/FakeVanity", "youtube.com/FakeVanity"),
        ("https://www.youtube.com/watch?v=fakevideo00", "a video"),
        ("https://youtu.be/fakevideo00", "a video"),
        ("https://www.youtube.com/shorts/fakevideo00", "a Short"),
        ("https://www.youtube.com/live/fakevideo00", "a live stream"),
        ("https://www.youtube.com/playlist?list=PLfake", "a playlist"),
        ("https://example.com/@fakehandle", "not a youtube.com link"),
        ("https://www.youtube.com/", "youtube.com itself"),
        ("https://www.youtube.com/channel/not-an-id", "without a channel id"),
        ("two words", "no spaces"),
        ("@", "empty handle"),
    ],
)
def test_refused_forms_say_why_and_what_instead(entry: str, says: str) -> None:
    with pytest.raises(EntryError) as caught:
        parse_entries([entry])

    assert says in str(caught.value)


def test_a_refusal_points_at_the_handle() -> None:
    with pytest.raises(EntryError, match="paste the @handle"):
        parse_entries(["https://www.youtube.com/c/FakeCustom"])


def test_every_bad_line_is_reported_at_once() -> None:
    text = (
        "@fakeone\n"
        "https://www.youtube.com/c/FakeCustom\n"
        "# a comment, skipped\n"
        "\n"
        "https://youtu.be/fakevideo00\n"
        "@faketwo\n"
        "two words\n"
    )

    with pytest.raises(EntryError) as caught:
        read_entries(text)

    assert [item.where for item in caught.value.invalid] == [
        "line 2",
        "line 5",
        "line 7",
    ]


def test_forms_and_case_collapse_into_one_lookup() -> None:
    entries = parse_entries(
        ["@FakeName", "fakename", "youtube.com/@FAKENAME", "@other"]
    )

    assert [entry.lookup for entry in entries] == [
        HandleLookup("fakename"),
        HandleLookup("other"),
    ]
    # The first form seen is the one reported.
    assert entries[0].text == "@FakeName"


def test_blank_lines_and_comments_are_skipped() -> None:
    entries = read_entries("# seeds\n\n@fakeone\n   # indented comment\n")

    assert [entry.where for entry in entries] == ["line 3"]


def test_a_comment_after_an_entry_is_skipped() -> None:
    """The failures file is written that way, and must read back."""
    entries = read_entries("@fakeone  # YouTube returns no channel for it\n")

    assert [entry.lookup for entry in entries] == [HandleLookup("fakeone")]


def test_a_fragment_is_not_a_comment() -> None:
    entries = read_entries("https://www.youtube.com/@fakeone#about\n")

    assert [entry.lookup for entry in entries] == [HandleLookup("fakeone")]
