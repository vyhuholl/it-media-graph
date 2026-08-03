"""Reading the four counters out of a stored payload.

Payload shapes here mirror what Telethon actually produces — checked
against the collected raw layer — because the whole value of this module
is that it agrees with the real thing.
"""

from typing import Any

from itgraph.derive.metrics import Counters, counters_of, reaction_key


def message(**fields: Any) -> dict[str, Any]:
    """A `Message` payload carrying only what a test cares about."""
    return {"_": "Message", "id": 1, **fields}


def emoji(emoticon: str, count: int) -> dict[str, Any]:
    return {
        "_": "ReactionCount",
        "count": count,
        "reaction": {"_": "ReactionEmoji", "emoticon": emoticon},
    }


def test_the_plain_counters_are_read() -> None:
    counters = counters_of(message(views=1200, forwards=14))

    assert counters is not None
    assert counters.views == 1200
    assert counters.forwards == 14


def test_a_service_message_is_not_a_post() -> None:
    """A "channel photo changed" event measures nothing.

    Recording it would put a row of nulls in the observation table and
    call it a reading.
    """
    assert counters_of({"_": "MessageService", "id": 7}) is None


def test_reactions_are_kept_per_emoji() -> None:
    counters = counters_of(
        message(
            reactions={
                "_": "MessageReactions",
                "results": [emoji("👍", 30), emoji("🤡", 9)],
            }
        )
    )

    assert counters is not None
    assert counters.reactions == {"👍": 30, "🤡": 9}


def test_a_channel_with_reactions_off_reads_as_absent() -> None:
    """Absent, not zero — the distinction the baselines depend on.

    The payload stores a missing field as JSON null, so this arrives as a
    null under a key that exists rather than as a missing key.
    """
    counters = counters_of(message(views=10, reactions=None))

    assert counters is not None
    assert counters.reactions is None


def test_a_post_nobody_reacted_to_reads_as_empty() -> None:
    """The other half of the same distinction.

    The channel does publish reactions; this post has none. That is a
    zero, and a zero belongs in the baseline.
    """
    counters = counters_of(
        message(reactions={"_": "MessageReactions", "results": []})
    )

    assert counters is not None
    assert counters.reactions == {}


def test_results_that_are_not_a_list_read_as_absent() -> None:
    counters = counters_of(
        message(reactions={"_": "MessageReactions", "results": None})
    )

    assert counters is not None
    assert counters.reactions is None


def test_a_custom_emoji_is_named_by_its_document() -> None:
    counters = counters_of(
        message(
            reactions={
                "_": "MessageReactions",
                "results": [
                    {
                        "_": "ReactionCount",
                        "count": 4,
                        "reaction": {
                            "_": "ReactionCustomEmoji",
                            "document_id": 5789,
                        },
                    }
                ],
            }
        )
    )

    assert counters is not None
    assert counters.reactions == {"custom:5789": 4}


def test_paid_reactions_are_one_bucket() -> None:
    assert reaction_key({"_": "ReactionPaid"}) == "paid"


def test_an_unrecognized_reaction_is_counted_not_dropped() -> None:
    """A type Telegram adds later still has to reach the total.

    Dropping it would understate the post silently, which is worse than
    a bucket nobody can name.
    """
    counters = counters_of(
        message(
            reactions={
                "_": "MessageReactions",
                "results": [
                    {
                        "_": "ReactionCount",
                        "count": 3,
                        "reaction": {"_": "ReactionFromTheFuture"},
                    },
                    emoji("👍", 1),
                ],
            }
        )
    )

    assert counters is not None
    assert counters.reactions == {"unknown": 3, "👍": 1}


def test_two_reactions_on_one_key_are_summed() -> None:
    counters = counters_of(
        message(
            reactions={
                "_": "MessageReactions",
                "results": [
                    {
                        "_": "ReactionCount",
                        "count": 2,
                        "reaction": {"_": "ReactionUnknownA"},
                    },
                    {
                        "_": "ReactionCount",
                        "count": 5,
                        "reaction": {"_": "ReactionUnknownB"},
                    },
                ],
            }
        )
    )

    assert counters is not None
    assert counters.reactions == {"unknown": 7}


def test_comments_come_from_the_reply_counter() -> None:
    counters = counters_of(
        message(
            replies={
                "_": "MessageReplies",
                "replies": 42,
                "comments": True,
                "channel_id": 1520237172,
            }
        )
    )

    assert counters is not None
    assert counters.comments == 42


def test_a_channel_without_a_discussion_group_has_no_comment_count() -> None:
    counters = counters_of(message(views=5, replies=None))

    assert counters is not None
    assert counters.comments is None


def test_zero_comments_is_not_absent() -> None:
    counters = counters_of(
        message(replies={"_": "MessageReplies", "replies": 0})
    )

    assert counters is not None
    assert counters.comments == 0


def test_a_post_with_no_view_count_reads_as_absent() -> None:
    counters = counters_of(message(forwards=1))

    assert counters is not None
    assert counters.views is None


def test_a_boolean_is_not_a_counter() -> None:
    """`bool` is a subclass of `int`, so this needs saying explicitly.

    A payload shape nobody expected should read as absent rather than as
    the number one.
    """
    counters = counters_of(message(views=True))

    assert counters is not None
    assert counters.views is None


def test_an_empty_message_reads_as_all_absent() -> None:
    assert counters_of(message()) == Counters()
