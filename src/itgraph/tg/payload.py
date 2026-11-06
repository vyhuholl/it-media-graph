"""Telethon objects as JSON-safe payloads.

The only place in the project that touches payload *shape*. A payload is
stored as received, with one exception: what names a person. No field is
dropped, renamed or interpreted — not that the bytes survive literally:
``jsonb`` cannot hold a datetime or a byte string, so those two types are
re-encoded.

The exception is ``without_people``. A post carries people who never
chose to be in this database — the latest commenters, the top paid
reactors, the name signed under it — and nothing here reads any of them.
They are removed before anything is stored, and a migration removed them
once from what was stored before. Channels stay wherever they appear: a
channel commenting under a post is the graph this project studies.
"""

import base64
from datetime import date, datetime
from typing import Any

__all__ = ["encode_payload", "json_safe", "without_people"]

# Telethon's types for a user's account. A `User` is one too, unless it is
# a bot: a channel's payload lists its bots, and a bot is not a person.
USER_TYPES = frozenset(
    {
        "PeerUser",
        "InputPeerUser",
        "InputPeerUserFromMessage",
        "InputUser",
        "InputUserFromMessage",
        "UserEmpty",
    }
)

# A mention of a user by id, in a post's entities. A mention by @handle is
# part of the text, which stays.
MENTION_TYPES = frozenset(
    {"MessageEntityMentionName", "InputMessageEntityMentionName"}
)

# Names signed under a post or carried by a forward, wherever they sit.
NAME_FIELDS = frozenset({"post_author", "from_name", "saved_from_name"})

# Lists of bare user ids, which carry no type to recognise them by.
USER_ID_LISTS = frozenset({"winners", "recent_requesters"})


def json_safe(value: Any) -> Any:
    """A value from ``.to_dict()``, in a form ``jsonb`` accepts.

    Datetimes become ISO-8601 strings and bytes become base64; both are
    recoverable, so this is re-encoding rather than parsing. Containers
    are walked, keys are left exactly as Telethon spelled them — including
    the ``_`` type tag, which is what makes the payload self-describing.

    Raises ``TypeError`` on a type Telethon has not produced before. That
    is deliberate: guessing at a new type would quietly corrupt history
    that is expensive to fetch again, and the caller records the channel
    as failed and moves on.
    """
    if isinstance(value, dict):
        return {key: json_safe(item) for key, item in value.items()}
    if isinstance(value, (list, tuple)):
        return [json_safe(item) for item in value]
    # Before the int branch: bool is a subclass of int, and datetime a
    # subclass of date.
    if isinstance(value, bool) or value is None:
        return value
    if isinstance(value, (datetime, date)):
        return value.isoformat()
    if isinstance(value, (bytes, bytearray, memoryview)):
        return base64.b64encode(bytes(value)).decode("ascii")
    if isinstance(value, (str, int, float)):
        return value
    raise TypeError(
        f"no JSON encoding for {type(value).__name__}; "
        "add one to json_safe rather than dropping the field"
    )


def _is_user(value: Any) -> bool:
    if not isinstance(value, dict):
        return False
    kind = value.get("_")
    return kind in USER_TYPES or (
        kind == "User" and value.get("bot") is not True
    )


def _names_someone_as_item(item: Any) -> bool:
    """Whether a list item is a person: a user, a mention, a user's reaction.

    A reactor or a reaction names its user through ``peer_id``; dropping
    only the peer would leave a count attributed to nobody, which is a
    person's spending all the same.
    """
    if _is_user(item):
        return True
    if not isinstance(item, dict):
        return False
    return item.get("_") in MENTION_TYPES or _is_user(item.get("peer_id"))


def without_people(value: Any) -> Any:
    """A JSON value with what names a person removed.

    Where it is a field it becomes null, so the payload keeps the shape
    Telethon gives every object; where it is an item of a list it is
    dropped. Everything else, channels included, is returned as it was,
    so a value that names nobody comes back equal. Takes and returns plain
    JSON values, and has no branch that raises.

    The migration that cleared what was stored before holds a copy of
    this in SQL, and ``tests/test_people.py`` holds the two equal.
    """
    if isinstance(value, dict):
        if _is_user(value):
            return None
        cleared: dict[str, Any] = {}
        for key, item in value.items():
            if key in NAME_FIELDS and item is not None:
                cleared[key] = None
            elif key in USER_ID_LISTS and isinstance(item, list):
                cleared[key] = []
            else:
                cleared[key] = without_people(item)
        return cleared
    if isinstance(value, list):
        return [
            without_people(item)
            for item in value
            if not _names_someone_as_item(item)
        ]
    return value


def encode_payload(obj: Any) -> dict[str, Any]:
    """A Telethon object as the payload stored in the raw layer."""
    encoded = without_people(json_safe(obj.to_dict()))
    if not isinstance(encoded, dict):  # pragma: no cover - defensive
        raise TypeError(f"{type(obj).__name__}.to_dict() is not a mapping")
    return encoded
