"""Reading references out of a payload, one shape at a time.

This is where derivation breaks — a peer shape misread, an entity offset
counted in the wrong units — so every shape gets its own case. No
database and no network: these are pure functions over JSON.
"""

from typing import Any

from itgraph.derive.references import (
    Reference,
    extract_references,
    forward_target,
    normalize_username,
    parse_tme_link,
    peer_channel_id,
)

SRC = 1000000001
DST = 1000000002


def message(**fields: Any) -> dict[str, Any]:
    """A minimal stored-message payload with the given fields set."""
    return {"_": "Message", "id": 42, "message": "", "entities": [], **fields}


# --- forward peers ---------------------------------------------------


def test_a_channel_peer_yields_its_id() -> None:
    assert peer_channel_id({"_": "PeerChannel", "channel_id": DST}) == DST


def test_a_user_peer_yields_nothing() -> None:
    assert peer_channel_id({"_": "PeerUser", "user_id": 777}) is None


def test_a_legacy_chat_peer_yields_nothing() -> None:
    assert peer_channel_id({"_": "PeerChat", "chat_id": 555}) is None


def test_a_missing_peer_yields_nothing() -> None:
    # A forward whose origin the author's privacy withholds.
    assert peer_channel_id(None) is None


def test_forward_from_a_channel_is_that_channel() -> None:
    payload = message(
        fwd_from={
            "_": "MessageFwdHeader",
            "from_id": {"_": "PeerChannel", "channel_id": DST},
        }
    )
    assert forward_target(payload, src_channel_id=SRC) == DST


def test_forward_from_a_user_is_nothing() -> None:
    payload = message(
        fwd_from={
            "_": "MessageFwdHeader",
            "from_id": {"_": "PeerUser", "user_id": 777},
        }
    )
    assert forward_target(payload, src_channel_id=SRC) is None


def test_forward_with_a_hidden_origin_is_nothing() -> None:
    payload = message(
        fwd_from={"_": "MessageFwdHeader", "from_id": None, "from_name": "X"}
    )
    assert forward_target(payload, src_channel_id=SRC) is None


def test_a_self_forward_is_nothing() -> None:
    payload = message(
        fwd_from={
            "_": "MessageFwdHeader",
            "from_id": {"_": "PeerChannel", "channel_id": SRC},
        }
    )
    assert forward_target(payload, src_channel_id=SRC) is None


def test_a_plain_message_is_no_forward() -> None:
    assert forward_target(message(), src_channel_id=SRC) is None


# --- entity mentions and links ---------------------------------------


def test_a_username_mention_is_extracted() -> None:
    payload = message(
        message="see @durov today",
        entities=[{"_": "MessageEntityMention", "offset": 4, "length": 6}],
    )
    assert extract_references(payload) == [Reference(username="durov")]


def test_an_emoji_before_a_mention_does_not_corrupt_it() -> None:
    """The whole reason offsets are counted in UTF-16 code units.

    Two emoji are two characters but four UTF-16 units; slicing the text
    by character index would hand back ``hannel`` instead of ``channel``.
    """
    payload = message(
        message="🔥🔥@channel",
        entities=[{"_": "MessageEntityMention", "offset": 4, "length": 8}],
    )
    assert extract_references(payload) == [Reference(username="channel")]


def test_a_mention_of_a_person_is_still_a_username_here() -> None:
    """Whether a username is a channel is decided later, against the DB.

    Extraction only reads the handle; a username that turns out to be a
    person is filtered when it fails to resolve, not here.
    """
    payload = message(
        message="thanks @someone",
        entities=[{"_": "MessageEntityMention", "offset": 8, "length": 8}],
    )
    assert extract_references(payload) == [Reference(username="someone")]


def test_a_plain_tme_url_entity_is_extracted() -> None:
    payload = message(
        message="https://t.me/durov",
        entities=[{"_": "MessageEntityUrl", "offset": 0, "length": 18}],
    )
    assert extract_references(payload) == [Reference(username="durov")]


def test_a_hyperlink_entity_reads_its_hidden_url() -> None:
    payload = message(
        message="click here",
        entities=[
            {
                "_": "MessageEntityTextUrl",
                "offset": 0,
                "length": 10,
                "url": "https://t.me/c/1234567890/55",
            }
        ],
    )
    assert extract_references(payload) == [Reference(channel_id=1234567890)]


def test_a_non_telegram_link_yields_nothing() -> None:
    payload = message(
        message="https://example.com/durov",
        entities=[{"_": "MessageEntityUrl", "offset": 0, "length": 25}],
    )
    assert extract_references(payload) == []


def test_a_message_without_entities_yields_nothing() -> None:
    assert extract_references(message(message="plain text")) == []


# --- t.me link forms -------------------------------------------------


def test_tme_name() -> None:
    assert parse_tme_link("t.me/durov") == Reference(username="durov")


def test_tme_name_message() -> None:
    # A link to one message still points at the channel.
    assert parse_tme_link("t.me/durov/123") == Reference(username="durov")


def test_tme_c_id_message() -> None:
    assert parse_tme_link("t.me/c/1234567890/55") == Reference(
        channel_id=1234567890
    )


def test_tme_c_id_without_a_message() -> None:
    assert parse_tme_link("https://t.me/c/1234567890") == Reference(
        channel_id=1234567890
    )


def test_tme_s_preview() -> None:
    assert parse_tme_link("https://t.me/s/durov") == Reference(
        username="durov"
    )


def test_tme_joinchat_is_an_invite() -> None:
    assert parse_tme_link("t.me/joinchat/AAAAAE1234") is None


def test_tme_plus_is_an_invite() -> None:
    assert parse_tme_link("https://t.me/+AbCdEf12345") is None


def test_a_scheme_and_www_are_tolerated() -> None:
    assert parse_tme_link("http://www.t.me/durov") == Reference(
        username="durov"
    )


def test_a_query_string_is_ignored() -> None:
    assert parse_tme_link("https://t.me/durov?start=x") == Reference(
        username="durov"
    )


def test_a_bare_host_references_nothing() -> None:
    assert parse_tme_link("https://t.me/") is None


# --- username normalization ------------------------------------------


def test_normalization_lowercases_and_strips_the_at() -> None:
    assert normalize_username("@Durov") == "durov"


def test_normalization_tolerates_surrounding_space() -> None:
    assert normalize_username("  @Example_Channel ") == "example_channel"


def test_a_too_short_handle_is_refused() -> None:
    assert normalize_username("@ab") is None


def test_a_handle_starting_with_a_digit_is_refused() -> None:
    assert normalize_username("123abc") is None
