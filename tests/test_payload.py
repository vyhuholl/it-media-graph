"""Turning Telethon objects into payloads `jsonb` will accept.

The raw layer is only worth what it preserves: re-fetching history is the
expensive operation the whole design avoids, so a field quietly dropped
here is a field lost for good.
"""

import base64
import json
from datetime import UTC, date, datetime
from typing import Any

import pytest
from fakes import FakeMessage, message_to_dict

from itgraph.tg.payload import encode_payload, json_safe


def key_paths(
    value: Any, prefix: tuple[str, ...] = ()
) -> set[tuple[str, ...]]:
    """Every key in a nested structure, as a path from the root.

    List elements collapse to a single ``[]`` step: what matters is that
    no *name* went missing, and two entities of the same shape would
    otherwise show up as two different paths.
    """
    paths: set[tuple[str, ...]] = set()
    if isinstance(value, dict):
        for key, item in value.items():
            paths.add((*prefix, key))
            paths |= key_paths(item, (*prefix, key))
    elif isinstance(value, list):
        for item in value:
            paths |= key_paths(item, (*prefix, "[]"))
    return paths


def test_no_key_is_dropped_or_renamed() -> None:
    original = message_to_dict()

    encoded = json_safe(original)

    assert key_paths(encoded) == key_paths(original)


def test_the_type_tag_survives() -> None:
    """`_` is what makes a stored payload self-describing.

    Without it nothing downstream can tell a photo from a document
    without guessing, and guessing is parsing.
    """
    encoded = json_safe(message_to_dict())

    assert encoded["_"] == "Message"
    assert encoded["media"]["photo"]["sizes"][0]["_"] == "PhotoStrippedSize"


def test_the_result_is_actually_serializable() -> None:
    # The point of the exercise: `jsonb` gets this or nothing.
    encoded = json_safe(message_to_dict())

    json.dumps(encoded)


def test_datetimes_become_iso_8601_at_every_depth() -> None:
    encoded = json_safe(message_to_dict())

    assert encoded["date"] == "2026-03-14T09:26:53+00:00"
    assert encoded["fwd_from"]["date"] == "2026-03-13T18:02:11+00:00"
    assert encoded["media"]["photo"]["date"] == "2026-03-14T09:26:50+00:00"


def test_a_datetime_round_trips() -> None:
    moment = datetime(2026, 3, 14, 9, 26, 53, tzinfo=UTC)

    assert datetime.fromisoformat(json_safe(moment)) == moment


def test_bytes_become_base64_and_round_trip() -> None:
    original = message_to_dict()
    reference = original["media"]["photo"]["file_reference"]

    encoded = json_safe(original)

    stored = encoded["media"]["photo"]["file_reference"]
    assert isinstance(stored, str)
    assert base64.b64decode(stored) == reference
    # Also inside a list, which is where a naive walker forgets to look.
    assert base64.b64decode(
        encoded["media"]["photo"]["sizes"][0]["bytes"]
    ) == (b"\x01\x1a")


def test_bytearray_and_memoryview_are_encoded_too() -> None:
    assert json_safe(bytearray(b"ab")) == json_safe(b"ab")
    assert json_safe(memoryview(b"ab")) == json_safe(b"ab")


def test_a_plain_date_is_encoded_not_mistaken_for_a_number() -> None:
    # datetime subclasses date, so the order of those checks matters.
    assert json_safe(date(2026, 3, 14)) == "2026-03-14"


def test_booleans_stay_booleans() -> None:
    """bool is a subclass of int, and `False` must not become `0`."""
    encoded = json_safe(message_to_dict())

    assert encoded["out"] is False
    assert encoded["silent"] is False


def test_none_and_empty_containers_are_preserved() -> None:
    encoded = json_safe(message_to_dict())

    # An absent edit_date means "never edited" — dropping the key would
    # make that indistinguishable from a field Telegram stopped sending.
    assert "edit_date" in encoded
    assert encoded["edit_date"] is None
    assert encoded["restriction_reason"] == []


def test_numbers_are_left_alone() -> None:
    """Telegram ids exceed 2^53; they must not go anywhere near a float."""
    encoded = json_safe(message_to_dict())

    assert encoded["media"]["photo"]["id"] == 5555555555555555555
    assert encoded["media"]["photo"]["access_hash"] == -1234567890123456789


def test_tuples_become_lists() -> None:
    assert json_safe(("a", "b")) == ["a", "b"]


def test_an_unknown_type_is_refused_not_guessed_at() -> None:
    """Silence here would corrupt history that costs hours to re-fetch.

    The caller records the channel as failed and carries on, which is
    recoverable; a payload with a stringified object in it is not.
    """

    class Unexpected:
        pass

    with pytest.raises(TypeError, match="no JSON encoding for Unexpected"):
        json_safe({"_": "Message", "surprise": Unexpected()})


def test_encode_payload_reads_to_dict() -> None:
    encoded = encode_payload(FakeMessage())

    assert encoded["id"] == 4242
    json.dumps(encoded)
