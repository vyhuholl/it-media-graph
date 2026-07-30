"""Reading an operator's list, one bad line at a time.

Pure functions over strings: no database, no network, no Telethon. The
point of these is that a mistake in a hand-written list is caught here,
where it costs nothing, rather than by a lookup that has already been
spent.
"""

from pathlib import Path

import pytest

from itgraph.derive.references import normalize_username
from itgraph.usernames import EntryError, parse_entries, read_entries

# --- the forms a person pastes ---------------------------------------


@pytest.mark.parametrize(
    "entry",
    [
        "fake_channel",
        "@fake_channel",
        "FAKE_Channel",
        "  @fake_channel  ",
        "t.me/fake_channel",
        "https://t.me/fake_channel",
        "https://t.me/s/fake_channel",
        "t.me/fake_channel/1234",
    ],
)
def test_every_accepted_form_is_the_same_username(entry: str) -> None:
    assert parse_entries([entry]) == ["fake_channel"]


def test_a_post_link_adds_the_channel_not_the_post() -> None:
    # The operator is adding a channel and pasted what was in their
    # clipboard; the message id is not a second thing to add.
    assert parse_entries(["t.me/fake_channel/99"]) == ["fake_channel"]


# --- what is refused, and by what name -------------------------------


@pytest.mark.parametrize(
    "entry",
    ["t.me/+AbCdEfGhIj", "https://t.me/+AbCdEfGhIj", "t.me/joinchat/AbCdEf"],
)
def test_an_invite_link_is_refused_as_an_invite(entry: str) -> None:
    with pytest.raises(EntryError) as caught:
        parse_entries([entry])
    assert "invite link" in caught.value.invalid[0].reason


def test_a_channel_id_link_is_refused_for_naming_no_username() -> None:
    with pytest.raises(EntryError) as caught:
        parse_entries(["t.me/c/1234567890/5"])
    assert "channel id" in caught.value.invalid[0].reason


@pytest.mark.parametrize("entry", ["ab", "9channel", "two words", "-"])
def test_what_cannot_be_a_username_is_refused(entry: str) -> None:
    with pytest.raises(EntryError):
        parse_entries([entry])


def test_a_non_telegram_link_is_refused() -> None:
    with pytest.raises(EntryError):
        parse_entries(["https://example.com/fake_channel"])


def test_the_refusal_names_the_entry_and_where_it_was() -> None:
    with pytest.raises(EntryError) as caught:
        parse_entries(["fake_channel", "ab"])
    (invalid,) = caught.value.invalid
    assert invalid.entry == "ab"
    assert invalid.where == "argument 2"


def test_the_error_message_describes_the_rule_the_project_enforces() -> None:
    # The message spells the shape out for the operator; the rule itself
    # lives in `derive.references`. This is what keeps the two in step.
    with pytest.raises(EntryError) as caught:
        parse_entries(["abc"])
    reason = caught.value.invalid[0].reason
    assert "4-32" in reason
    assert normalize_username("abc") is None
    assert normalize_username("abcd") == "abcd"


# --- de-duplication ---------------------------------------------------


def test_duplicates_differing_in_case_and_form_cost_one_lookup() -> None:
    assert parse_entries(
        ["fake_channel", "@FAKE_CHANNEL", "https://t.me/Fake_Channel"]
    ) == ["fake_channel"]


def test_first_seen_order_is_preserved() -> None:
    # What makes a `--limit` run cover the same entries in the same order
    # when it is repeated.
    assert parse_entries(["second_fake", "first_fake", "second_fake"]) == [
        "second_fake",
        "first_fake",
    ]


# --- files ------------------------------------------------------------


def test_a_file_is_read_a_username_per_line(tmp_path: Path) -> None:
    listing = tmp_path / "channels.txt"
    listing.write_text("fake_one\n@fake_two\nt.me/fake_three\n")
    assert read_entries(listing) == ["fake_one", "fake_two", "fake_three"]


def test_blank_lines_and_comments_are_skipped(tmp_path: Path) -> None:
    listing = tmp_path / "channels.txt"
    listing.write_text(
        "# a list of channels\n"
        "fake_one\n"
        "\n"
        "   \n"
        "  # fake_two — dead, checked 2026-07-30\n"
        "fake_three\n"
    )
    assert read_entries(listing) == ["fake_one", "fake_three"]


def test_every_bad_line_is_reported_in_one_run(tmp_path: Path) -> None:
    listing = tmp_path / "channels.txt"
    listing.write_text("fake_one\nab\nfake_two\nt.me/+AbCdEf\n-\n")
    with pytest.raises(EntryError) as caught:
        read_entries(listing)
    assert [item.where for item in caught.value.invalid] == [
        "line 2",
        "line 4",
        "line 5",
    ]


def test_a_bad_line_is_reported_by_its_number_in_the_file(
    tmp_path: Path,
) -> None:
    # Counted against the file, not against the entries: a comment and a
    # blank line before the mistake must not shift its number.
    listing = tmp_path / "channels.txt"
    listing.write_text("# heading\n\nfake_one\nab\n")
    with pytest.raises(EntryError) as caught:
        read_entries(listing)
    assert caught.value.invalid[0].where == "line 4"


def test_an_empty_file_yields_nothing(tmp_path: Path) -> None:
    listing = tmp_path / "channels.txt"
    listing.write_text("# nothing here yet\n")
    assert read_entries(listing) == []
