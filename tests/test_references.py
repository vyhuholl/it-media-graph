"""Reading references out of a payload, one shape at a time.

This is where derivation breaks — a peer shape misread, an entity offset
counted in the wrong units — so every shape gets its own case. No
database and no network: these are pure functions over JSON.
"""

from datetime import datetime
from typing import Any

import pytest

from itgraph.derive.references import (
    Forward,
    Reference,
    extract_references,
    extract_text_references,
    forward_target,
    normalize_username,
    parse_tme_link,
    peer_channel_id,
)

SRC = 1000000001
DST = 1000000002

ORIGINAL_DATE = "2026-03-14T09:26:53+00:00"


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
    forward = forward_target(payload, src_channel_id=SRC)
    assert forward is not None
    assert forward.channel_id == DST


def test_a_forward_carries_the_original_post_id_and_date() -> None:
    payload = message(
        fwd_from={
            "_": "MessageFwdHeader",
            "from_id": {"_": "PeerChannel", "channel_id": DST},
            "channel_post": 555,
            "date": ORIGINAL_DATE,
        }
    )
    assert forward_target(payload, src_channel_id=SRC) == Forward(
        channel_id=DST,
        msg_id=555,
        published_at=datetime.fromisoformat(ORIGINAL_DATE),
    )


def test_a_forward_naming_no_original_post_still_yields_a_forward() -> None:
    # Channel and its date are known, the post id is not — still an edge,
    # with the post id left empty.
    payload = message(
        fwd_from={
            "_": "MessageFwdHeader",
            "from_id": {"_": "PeerChannel", "channel_id": DST},
            "date": ORIGINAL_DATE,
        }
    )
    assert forward_target(payload, src_channel_id=SRC) == Forward(
        channel_id=DST,
        msg_id=None,
        published_at=datetime.fromisoformat(ORIGINAL_DATE),
    )


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
    assert extract_references(payload) == [
        Reference(channel_id=1234567890, msg_id=55)
    ]


def test_two_links_to_different_posts_of_one_channel_are_two_references() -> (
    None
):
    # Two references, not one: the deduplication that collapses a repeat
    # keys on the post, so different posts survive it.
    payload = message(
        message="https://t.me/durov/10 https://t.me/durov/20",
        entities=[
            {"_": "MessageEntityUrl", "offset": 0, "length": 21},
            {"_": "MessageEntityUrl", "offset": 22, "length": 21},
        ],
    )
    assert extract_references(payload) == [
        Reference(username="durov", msg_id=10),
        Reference(username="durov", msg_id=20),
    ]


def test_a_mention_and_a_post_link_to_one_channel_are_two_references() -> None:
    # One names no post, one names a post — two distinct references to the
    # same channel.
    payload = message(
        message="@durov https://t.me/durov/5",
        entities=[
            {"_": "MessageEntityMention", "offset": 0, "length": 6},
            {"_": "MessageEntityUrl", "offset": 7, "length": 20},
        ],
    )
    assert extract_references(payload) == [
        Reference(username="durov"),
        Reference(username="durov", msg_id=5),
    ]


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
    # A link to one message points at the channel and carries the post id.
    assert parse_tme_link("t.me/durov/123") == Reference(
        username="durov", msg_id=123
    )


def test_tme_c_id_message() -> None:
    assert parse_tme_link("t.me/c/1234567890/55") == Reference(
        channel_id=1234567890, msg_id=55
    )


def test_tme_c_id_without_a_message() -> None:
    # No message segment — the channel, with no post id.
    assert parse_tme_link("https://t.me/c/1234567890") == Reference(
        channel_id=1234567890, msg_id=None
    )


def test_tme_s_preview() -> None:
    assert parse_tme_link("https://t.me/s/durov") == Reference(
        username="durov"
    )


def test_tme_joinchat_is_an_invite() -> None:
    assert parse_tme_link("t.me/joinchat/AAAAAE1234") is None


def test_tme_plus_is_an_invite() -> None:
    assert parse_tme_link("https://t.me/+AbCdEf12345") is None


def test_tme_addlist_is_a_folder_not_a_channel() -> None:
    # Both spellings: the slug in the path, and the slug in the query
    # string, which leaves the reserved word alone in the path.
    assert parse_tme_link("https://t.me/addlist/AbCdEf12345") is None
    assert parse_tme_link("https://t.me/addlist?slug=AbCdEf12345") is None


def test_tme_addstickers_is_a_sticker_pack_not_a_channel() -> None:
    assert parse_tme_link("https://t.me/addstickers/SomePack") is None


@pytest.mark.parametrize(
    "url",
    [
        "https://t.me/addemoji/SomePack",
        "https://t.me/addtheme/SomeTheme",
        "https://t.me/setlanguage/some_lang",
        "https://t.me/share/url?url=https://example.com",
        "https://t.me/proxy?server=example.com",
        "https://t.me/socks?server=example.com",
        "https://t.me/login/12345",
        "https://t.me/confirmphone?phone=1&hash=x",
        "https://t.me/invoice/AbCdEf12345",
        "https://t.me/giftcode/AbCdEf12345",
        "https://t.me/contact/AbCdEf12345",
        "https://t.me/boost/durov",
    ],
)
def test_a_service_link_is_not_a_channel(url: str) -> None:
    assert parse_tme_link(url) is None


def test_a_service_word_is_reserved_in_the_preview_form_too() -> None:
    assert parse_tme_link("https://t.me/s/addstickers") is None


def test_a_service_word_is_reserved_whatever_its_case() -> None:
    assert parse_tme_link("https://t.me/AddStickers/SomePack") is None


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


# --- plain text, the shape a channel description arrives in -----------
#
# `ChannelFull.about` carries no entities, so the entity reader above
# finds nothing in it however many links it holds. Measured over real
# descriptions the `@mention` is the dominant form — 155 against 34
# `t.me`-shaped substrings — which is why it leads here.


def test_a_mention_in_a_description() -> None:
    assert extract_text_references("Подкаст: @example_podcast") == [
        Reference(username="example_podcast")
    ]


def test_a_bare_link_in_a_description() -> None:
    assert extract_text_references("см. t.me/example_podcast") == [
        Reference(username="example_podcast")
    ]


def test_a_description_holding_both_forms() -> None:
    assert extract_text_references(
        "Основной канал @example_main, подкаст https://t.me/example_podcast"
    ) == [
        Reference(username="example_podcast"),
        Reference(username="example_main"),
    ]


def test_a_link_ending_a_sentence_keeps_its_channel() -> None:
    """The full stop belongs to the sentence, not to the username."""
    assert extract_text_references("Читайте t.me/example_podcast.") == [
        Reference(username="example_podcast")
    ]


def test_a_link_in_brackets_keeps_its_channel() -> None:
    assert extract_text_references("Подкаст (t.me/example_podcast)") == [
        Reference(username="example_podcast")
    ]


def test_a_link_naming_a_post_carries_it() -> None:
    assert extract_text_references("t.me/example_main/42") == [
        Reference(username="example_main", msg_id=42)
    ]


def test_an_invite_link_in_a_description_references_nothing() -> None:
    assert extract_text_references("Чат: t.me/+AbCdEfGhIjK") == []


def test_a_service_path_in_a_description_references_nothing() -> None:
    assert extract_text_references("Папка: t.me/addlist/AbCdEf") == []


def test_a_service_word_written_as_a_mention_references_nothing() -> None:
    assert extract_text_references("@addstickers") == []


def test_a_non_telegram_link_references_nothing() -> None:
    assert extract_text_references("Сайт: https://example.com/example") == []


def test_a_description_with_nothing_in_it() -> None:
    assert extract_text_references("Просто описание без ссылок") == []


def test_an_empty_description() -> None:
    assert extract_text_references("") == []


def test_a_mention_too_short_to_be_a_username() -> None:
    assert extract_text_references("@ab") == []


def test_the_same_channel_twice_is_two_references() -> None:
    """Deduplication is the caller's job, exactly as for the entity
    reader — one channel referenced twice is two references here."""
    assert extract_text_references("@example_main и t.me/example_main") == [
        Reference(username="example_main"),
        Reference(username="example_main"),
    ]
