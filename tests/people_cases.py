"""Payloads that name people, and what each becomes once they are removed.

One table, read by every test of the rule: the collector's (`tg/payload.py`),
the migration's copy in SQL, and the restore's read-back. Shapes are
Telethon's own `to_dict()` output, checked against Telethon 1.44, but for
one container that exists only to put every type of a user's account
somewhere. Every id and name is invented, and spelled to look it.
"""

import json
from dataclasses import dataclass
from pathlib import Path
from types import ModuleType
from typing import Any

from alembic.config import Config
from alembic.script import ScriptDirectory

USER = 777000111
OTHER_USER = 777000222
CHANNEL = 1000000077


def peer_user(user_id: int = USER) -> dict[str, Any]:
    return {"_": "PeerUser", "user_id": user_id}


def peer_channel(channel_id: int = CHANNEL) -> dict[str, Any]:
    return {"_": "PeerChannel", "channel_id": channel_id}


def post(**fields: Any) -> dict[str, Any]:
    """A channel's post as Telethon writes one: every field present."""
    return {
        "_": "Message",
        "id": 10,
        "peer_id": peer_channel(1000000001),
        "date": "2026-11-05T09:00:00+00:00",
        "message": "a public post that mentions @fake_handle",
        "views": 123,
        "forwards": 4,
        "from_id": None,
        "post_author": None,
        "fwd_from": None,
        "reply_to": None,
        "media": None,
        "replies": None,
        "reactions": None,
        "entities": [],
        **fields,
    }


@dataclass(frozen=True, slots=True)
class Case:
    name: str
    received: dict[str, Any]
    cleared: dict[str, Any]


CASES: tuple[Case, ...] = (
    Case(
        "a post's latest commenters",
        post(
            replies={
                "_": "MessageReplies",
                "replies": 4,
                "replies_pts": 1,
                "comments": True,
                "recent_repliers": [
                    peer_user(),
                    peer_user(OTHER_USER),
                    peer_channel(),
                ],
                "channel_id": 1000000099,
                "max_id": None,
                "read_max_id": None,
            }
        ),
        post(
            replies={
                "_": "MessageReplies",
                "replies": 4,
                "replies_pts": 1,
                "comments": True,
                "recent_repliers": [peer_channel()],
                "channel_id": 1000000099,
                "max_id": None,
                "read_max_id": None,
            }
        ),
    ),
    Case(
        "its paid reactors and the reactions of users",
        post(
            reactions={
                "_": "MessageReactions",
                "results": [
                    {
                        "_": "ReactionCount",
                        "reaction": {"_": "ReactionEmoji", "emoticon": "x"},
                        "count": 5,
                        "chosen_order": None,
                    },
                    {
                        "_": "ReactionCount",
                        "reaction": {"_": "ReactionPaid"},
                        "count": 19,
                        "chosen_order": None,
                    },
                ],
                "min": None,
                "can_see_list": None,
                "reactions_as_tags": None,
                "recent_reactions": [
                    {
                        "_": "MessagePeerReaction",
                        "peer_id": peer_user(),
                        "date": None,
                        "reaction": {"_": "ReactionEmoji", "emoticon": "x"},
                        "big": None,
                        "unread": None,
                        "my": None,
                    }
                ],
                "top_reactors": [
                    {
                        "_": "MessageReactor",
                        "count": 10,
                        "top": True,
                        "my": None,
                        "anonymous": None,
                        "peer_id": peer_user(),
                    },
                    {
                        "_": "MessageReactor",
                        "count": 7,
                        "top": True,
                        "my": None,
                        "anonymous": None,
                        "peer_id": peer_channel(),
                    },
                    {
                        "_": "MessageReactor",
                        "count": 2,
                        "top": None,
                        "my": None,
                        "anonymous": True,
                        "peer_id": None,
                    },
                ],
            }
        ),
        post(
            reactions={
                "_": "MessageReactions",
                "results": [
                    {
                        "_": "ReactionCount",
                        "reaction": {"_": "ReactionEmoji", "emoticon": "x"},
                        "count": 5,
                        "chosen_order": None,
                    },
                    {
                        "_": "ReactionCount",
                        "reaction": {"_": "ReactionPaid"},
                        "count": 19,
                        "chosen_order": None,
                    },
                ],
                "min": None,
                "can_see_list": None,
                "reactions_as_tags": None,
                "recent_reactions": [],
                "top_reactors": [
                    {
                        "_": "MessageReactor",
                        "count": 7,
                        "top": True,
                        "my": None,
                        "anonymous": None,
                        "peer_id": peer_channel(),
                    },
                    {
                        "_": "MessageReactor",
                        "count": 2,
                        "top": None,
                        "my": None,
                        "anonymous": True,
                        "peer_id": None,
                    },
                ],
            }
        ),
    ),
    Case(
        "who wrote it",
        post(
            post_author="A Fake Author",
            from_id=peer_user(),
            fwd_from={
                "_": "MessageFwdHeader",
                "date": "2026-11-04T09:00:00+00:00",
                "imported": None,
                "saved_out": None,
                "from_id": peer_user(OTHER_USER),
                "from_name": "A Fake Sender",
                "channel_post": None,
                "post_author": "Another Fake Author",
                "saved_from_peer": peer_user(),
                "saved_from_msg_id": 3,
                "saved_from_id": peer_user(OTHER_USER),
                "saved_from_name": "A Fake Saver",
                "saved_date": None,
                "psa_type": None,
            },
        ),
        post(
            post_author=None,
            from_id=None,
            fwd_from={
                "_": "MessageFwdHeader",
                "date": "2026-11-04T09:00:00+00:00",
                "imported": None,
                "saved_out": None,
                "from_id": None,
                "from_name": None,
                "channel_post": None,
                "post_author": None,
                "saved_from_peer": None,
                "saved_from_msg_id": 3,
                "saved_from_id": None,
                "saved_from_name": None,
                "saved_date": None,
                "psa_type": None,
            },
        ),
    ),
    Case(
        "mentions by id",
        post(
            entities=[
                {
                    "_": "MessageEntityMentionName",
                    "offset": 0,
                    "length": 1,
                    "user_id": USER,
                },
                {
                    "_": "InputMessageEntityMentionName",
                    "offset": 2,
                    "length": 1,
                    "user_id": {
                        "_": "InputUser",
                        "user_id": OTHER_USER,
                        "access_hash": 5,
                    },
                },
                {"_": "MessageEntityUrl", "offset": 4, "length": 8},
                {"_": "MessageEntityMention", "offset": 28, "length": 12},
            ]
        ),
        post(
            entities=[
                {"_": "MessageEntityUrl", "offset": 4, "length": 8},
                {"_": "MessageEntityMention", "offset": 28, "length": 12},
            ]
        ),
    ),
    Case(
        "a poll's voters",
        post(
            media={
                "_": "MessageMediaPoll",
                "poll": {"_": "Poll", "id": 1, "question": "a fake poll"},
                "results": {
                    "_": "PollResults",
                    "min": None,
                    "results": [
                        {
                            "_": "PollAnswerVoters",
                            "option": "MA==",
                            "chosen": None,
                            "correct": None,
                            "voters": 3,
                            "recent_voters": [peer_user()],
                        }
                    ],
                    "total_voters": 3,
                    "recent_voters": [peer_user(), peer_user(OTHER_USER)],
                },
            }
        ),
        post(
            media={
                "_": "MessageMediaPoll",
                "poll": {"_": "Poll", "id": 1, "question": "a fake poll"},
                "results": {
                    "_": "PollResults",
                    "min": None,
                    "results": [
                        {
                            "_": "PollAnswerVoters",
                            "option": "MA==",
                            "chosen": None,
                            "correct": None,
                            "voters": 3,
                            "recent_voters": [],
                        }
                    ],
                    "total_voters": 3,
                    "recent_voters": [],
                },
            }
        ),
    ),
    Case(
        "a giveaway's winners",
        post(
            media={
                "_": "MessageMediaGiveawayResults",
                "channel_id": 1000000001,
                "launch_msg_id": 2,
                "winners_count": 2,
                "unclaimed_count": 0,
                "winners": [USER, OTHER_USER],
                "until_date": None,
            }
        ),
        post(
            media={
                "_": "MessageMediaGiveawayResults",
                "channel_id": 1000000001,
                "launch_msg_id": 2,
                "winners_count": 2,
                "unclaimed_count": 0,
                "winners": [],
                "until_date": None,
            }
        ),
    ),
    Case(
        "a reply to a user, at a path no list of paths named",
        post(
            reply_to={
                "_": "MessageReplyHeader",
                "reply_to_msg_id": 9,
                "reply_to_peer_id": peer_user(),
                "reply_from": {
                    "_": "MessageFwdHeader",
                    "from_id": peer_user(OTHER_USER),
                    "from_name": None,
                },
            }
        ),
        post(
            reply_to={
                "_": "MessageReplyHeader",
                "reply_to_msg_id": 9,
                "reply_to_peer_id": None,
                "reply_from": {
                    "_": "MessageFwdHeader",
                    "from_id": None,
                    "from_name": None,
                },
            }
        ),
    ),
    Case(
        "every type of a user's account, as a field and in a list",
        post(
            media={
                "_": "FakeContainer",
                "a": {"_": "InputPeerUser", "user_id": USER, "access_hash": 1},
                "b": {
                    "_": "InputUserFromMessage",
                    "peer": {"_": "InputPeerChannel", "channel_id": CHANNEL},
                    "msg_id": 1,
                    "user_id": USER,
                },
                "c": [
                    {
                        "_": "InputPeerUserFromMessage",
                        "peer": {
                            "_": "InputPeerChannel",
                            "channel_id": CHANNEL,
                        },
                        "msg_id": 1,
                        "user_id": OTHER_USER,
                    },
                    {"_": "UserEmpty", "id": OTHER_USER},
                    peer_channel(),
                ],
            }
        ),
        post(
            media={
                "_": "FakeContainer",
                "a": None,
                "b": None,
                "c": [peer_channel()],
            }
        ),
    ),
    Case(
        "a channel's extended information",
        {
            "_": "messages.ChatFull",
            "full_chat": {
                "_": "ChannelFull",
                "id": 1000000001,
                "about": "a fake channel about things",
                "participants_count": 4321,
                "requests_pending": 2,
                "recent_requesters": [USER, OTHER_USER],
            },
            "chats": [
                {"_": "Channel", "id": 1000000001, "creator": False},
            ],
            "users": [
                {
                    "_": "User",
                    "id": 555000001,
                    "bot": True,
                    "first_name": "Fake Bot",
                },
                {
                    "_": "User",
                    "id": USER,
                    "bot": None,
                    "first_name": "A Fake Person",
                },
                {
                    "_": "User",
                    "id": OTHER_USER,
                    "first_name": "Another Fake Person",
                },
                {"_": "UserEmpty", "id": 777000333},
            ],
        },
        {
            "_": "messages.ChatFull",
            "full_chat": {
                "_": "ChannelFull",
                "id": 1000000001,
                "about": "a fake channel about things",
                "participants_count": 4321,
                "requests_pending": 2,
                "recent_requesters": [],
            },
            "chats": [
                {"_": "Channel", "id": 1000000001, "creator": False},
            ],
            "users": [
                {
                    "_": "User",
                    "id": 555000001,
                    "bot": True,
                    "first_name": "Fake Bot",
                },
            ],
        },
    ),
)


def one_of_each() -> list[tuple[str, dict[str, Any]]]:
    """A post per type and field the collector's rule names, holding it alone.

    Built from `tg/payload.py`'s own constants, so that the migration's copy,
    its prefilter and the restore's read-back are each held to every one of
    them, and a type added there and nowhere else fails the suite.
    """
    from itgraph.tg.payload import (
        MENTION_TYPES,
        NAME_FIELDS,
        USER_ID_LISTS,
        USER_TYPES,
    )

    built: list[tuple[str, dict[str, Any]]] = []
    for kind in sorted(USER_TYPES | {"User"}):
        built.append(
            (
                kind,
                post(
                    media={
                        "_": "FakeContainer",
                        "one": {"_": kind, "id": USER},
                        "many": [
                            {"_": kind, "id": OTHER_USER},
                            peer_channel(),
                        ],
                    }
                ),
            )
        )
    for kind in sorted(MENTION_TYPES):
        built.append(
            (
                kind,
                post(
                    entities=[
                        {"_": kind, "offset": 0, "length": 1, "user_id": USER}
                    ]
                ),
            )
        )
    for field in sorted(NAME_FIELDS):
        built.append(
            (field, post(media={"_": "FakeContainer", field: "A Fake Name"}))
        )
    for field in sorted(USER_ID_LISTS):
        built.append(
            (field, post(media={"_": "FakeContainer", field: [USER]}))
        )
    return built


ONE_OF_EACH = one_of_each()


def newest_rule() -> ModuleType:
    """The newest revision carrying the rule in SQL: what stored rows met last.

    Its `FUNCTIONS` create the rule in `pg_temp`; its `TABLES` are what it
    clears; its `NAMES_SOMEONE` is the JSON path of its prefilter.
    """
    root = Path(__file__).parent.parent
    script = ScriptDirectory.from_config(Config(str(root / "alembic.ini")))
    for revision in script.walk_revisions():
        if "itgraph_without_people" in Path(revision.path).read_text():
            module: ModuleType = revision.module
            return module
    raise AssertionError("no revision carries the rule")  # pragma: no cover


def fields_naming_people() -> dict[str, Any]:
    """A post's fields that name people, merged out of the cases above.

    For a fake message whose payload a collector stores: one post carrying
    commenters, paid reactors, a signature, a user as author, a forward
    from a user and mentions by id at once.
    """
    by_name = {case.name: case.received for case in CASES}
    return {
        "replies": by_name["a post's latest commenters"]["replies"],
        "reactions": by_name["its paid reactors and the reactions of users"][
            "reactions"
        ],
        "post_author": by_name["who wrote it"]["post_author"],
        "from_id": by_name["who wrote it"]["from_id"],
        "fwd_from": by_name["who wrote it"]["fwd_from"],
        "entities": by_name["mentions by id"]["entities"],
    }


def names_nobody(payload: Any) -> bool:
    """Whether a stored payload is free of every person in the table."""
    text = json.dumps(payload)
    return not any(
        mark in text
        for mark in (
            str(USER),
            str(OTHER_USER),
            "PeerUser",
            "A Fake",
            "Another Fake",
        )
    )


# Payloads that name nobody, which the rule must return equal.
UNTOUCHED: tuple[Case, ...] = tuple(
    Case(name, payload, payload)
    for name, payload in (
        (
            "a forward from a channel",
            post(
                fwd_from={
                    "_": "MessageFwdHeader",
                    "date": "2026-11-04T09:00:00+00:00",
                    "from_id": peer_channel(),
                    "from_name": None,
                    "channel_post": 99,
                    "post_author": None,
                }
            ),
        ),
        (
            "a page linked from a post",
            post(
                media={
                    "_": "MessageMediaWebPage",
                    "webpage": {
                        "_": "WebPage",
                        "id": 1,
                        "url": "https://example.org/fake",
                        "site_name": "Example",
                        "author": "A Page Author",
                        "cached_page": {
                            "_": "Page",
                            "blocks": [
                                {
                                    "_": "PageBlockAuthorDate",
                                    "author": {
                                        "_": "TextPlain",
                                        "text": "A Page Author",
                                    },
                                    "published_date": None,
                                }
                            ],
                        },
                    },
                }
            ),
        ),
        ("a post that names nobody", post()),
        (
            "a service message",
            {
                "_": "MessageService",
                "id": 11,
                "date": "2026-11-05T09:00:00+00:00",
                "action": {"_": "MessageActionChatEditPhoto"},
            },
        ),
    )
)

ALL: tuple[Case, ...] = CASES + UNTOUCHED
