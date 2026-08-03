"""A stored payload, read as the four counters worth watching.

Pure: a mapping in, a small dataclass out. No network, no session, no
database — the same shape as ``derive/references.py``, and for the same
reason. What a payload means is a question that will be re-asked over
data already collected, so the answer must be re-computable and testable
without any of the machinery that fetched it.

The four are deliberately not collapsed into one number. Views are reach,
reactions are approval, forwards are an endorsement strong enough to
republish, and comments are as often an argument as an interest. They
move on different timescales and mean different things, so they are
carried separately here and scored separately later.
"""

from collections.abc import Mapping
from dataclasses import dataclass
from typing import Any

__all__ = ["Counters", "counters_of", "reaction_key"]

# The payload's type tag. A channel's own posts are `Message`; a
# `MessageService` row is an event — "channel photo changed", "pinned" —
# and has no counters to read.
MESSAGE = "Message"

# How a reaction that is not a plain emoji is named in the stored
# mapping. Prefixed rather than bare so a custom emoji's id can never
# collide with an emoticon, and so a reader can tell them apart.
CUSTOM_PREFIX = "custom:"
PAID = "paid"
UNKNOWN = "unknown"


@dataclass(frozen=True, slots=True)
class Counters:
    """One reading of one post's engagement.

    Every field is nullable, and ``None`` means *absent*, never zero. The
    distinction is the whole reason this type exists rather than a tuple
    of ints: a channel with reactions switched off publishes no reactions
    object at all, and a channel whose post nobody has reacted to
    publishes an empty one. Conflating them makes a baseline out of
    channels that were never playing, and everything divided by that
    baseline explodes — which is exactly what
    ``notebooks/anomalous_posts.py`` has to correct for per channel,
    because the single snapshot it reads had already lost the difference.
    """

    views: int | None = None
    forwards: int | None = None
    reactions: dict[str, int] | None = None
    comments: int | None = None


def reaction_key(reaction: Mapping[str, Any]) -> str:
    """What to call one reaction in the stored mapping.

    A plain emoji is stored as itself, so a reader needs no lookup table
    to see that a post collected clowns rather than hearts — which is the
    distinction the per-emoji breakdown exists to preserve. A custom
    emoji has no printable form here, only a document id, so it is stored
    under that with a prefix that cannot collide with an emoticon. Paid
    reactions are one bucket: they are stars, and which star is not a
    thing.
    """
    kind = reaction.get("_")
    if kind == "ReactionEmoji":
        emoticon = reaction.get("emoticon")
        return emoticon if isinstance(emoticon, str) else UNKNOWN
    if kind == "ReactionCustomEmoji":
        return f"{CUSTOM_PREFIX}{reaction.get('document_id')}"
    if kind == "ReactionPaid":
        return PAID
    # A reaction type Telegram adds later. Counted rather than dropped:
    # the total still has to be right, and a bucket nobody recognizes is
    # a better outcome than a silently missing count.
    return UNKNOWN


def _reactions_of(payload: Mapping[str, Any]) -> dict[str, int] | None:
    """The per-emoji counts, or ``None`` if this channel publishes none.

    ``results`` is checked for being a list rather than assumed to be
    one. The payload stores an absent field as JSON ``null``, so a
    channel with reactions off arrives here as a null under a key that
    exists — the same trap that makes ``jsonb_array_elements`` raise in
    the SQL version of this, rather than return nothing.

    An empty mapping is a real answer and not the same as ``None``: it
    means the channel does publish reactions and nobody has left one.
    """
    reactions = payload.get("reactions")
    if not isinstance(reactions, Mapping):
        return None
    results = reactions.get("results")
    if not isinstance(results, list):
        return None

    counts: dict[str, int] = {}
    for entry in results:
        if not isinstance(entry, Mapping):
            continue
        count = entry.get("count")
        reaction = entry.get("reaction")
        if not isinstance(count, int) or not isinstance(reaction, Mapping):
            continue
        # Summed rather than assigned: two entries could in principle
        # land on one key — an unrecognized type, or a custom emoji with
        # no id — and the count has to survive that.
        counts[reaction_key(reaction)] = (
            counts.get(reaction_key(reaction), 0) + count
        )
    return counts


def _int_or_none(value: Any) -> int | None:
    """An integer field, or ``None`` where the payload has nothing.

    ``bool`` is rejected explicitly because it is a subclass of ``int``
    and no counter is ever a boolean; letting one through would store
    ``True`` as 1 and hide a payload shape nobody expected.
    """
    if isinstance(value, bool) or not isinstance(value, int):
        return None
    return value


def counters_of(payload: Mapping[str, Any]) -> Counters | None:
    """The four counters of one stored message, or ``None`` if it is not one.

    ``None`` is returned for anything that is not a `Message` — a service
    event has no views, and a snapshot of it would be a row of nulls
    claiming to be an observation. Nothing was measured, so nothing is
    recorded.
    """
    if payload.get("_") != MESSAGE:
        return None

    replies = payload.get("replies")
    comments = (
        _int_or_none(replies.get("replies"))
        if isinstance(replies, Mapping)
        else None
    )

    return Counters(
        views=_int_or_none(payload.get("views")),
        forwards=_int_or_none(payload.get("forwards")),
        reactions=_reactions_of(payload),
        comments=comments,
    )
