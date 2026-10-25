"""Operator-supplied YouTube channel references, read before anything is spent.

The YouTube door for a hand-written list, the way ``usernames.py`` is
Telegram's: the forms a person actually pastes, turned into the lookups
``channels.list`` can make. Pure — no network, no database.

Three lookups exist, and only three: a channel id (``id``, fifty to a
call), a handle (``forHandle``), a legacy ``/user/`` name
(``forUsername``). Everything else is refused here, with the line number
and what to paste instead, and refused *together* — a list assembled by
a language model is exactly where the third bad line is found only after
the first two are fixed, unless all three are named at once.

Two refusals deserve their reasons. A ``/c/NAME`` custom URL and a bare
``youtube.com/NAME`` have no API method at all, and guessing that the
name is also the handle can land on someone else's channel. A video link
could be resolved through ``videos.list``, but that is a second path to a
channel for a case the operator fixes by copying one more thing.
"""

import re
from collections.abc import Iterable
from dataclasses import dataclass
from urllib.parse import unquote, urlsplit

from itgraph.usernames import EntryError, InvalidEntry

__all__ = [
    "ChannelIdLookup",
    "Entry",
    "EntryError",
    "HandleLookup",
    "LegacyNameLookup",
    "Lookup",
    "parse_entries",
    "read_entries",
]

# The shape the inventory's check holds an id to; see `YtChannel`.
CHANNEL_ID = re.compile(r"^UC[0-9A-Za-z_-]{22}$")

_TRAILING_COMMENT = re.compile(r"\s+#")

_HOSTS = {"youtube.com", "www.youtube.com", "m.youtube.com"}
_LINK_STARTS = ("http:", "https:", "youtu.be", *sorted(_HOSTS))

# First path segments that name something other than a channel.
_NOT_A_CHANNEL = {
    "watch": "a video",
    "shorts": "a Short",
    "live": "a live stream",
    "embed": "an embedded video",
    "v": "a video",
    "playlist": "a playlist",
    "results": "a search",
    "feed": "a feed",
    "hashtag": "a hashtag page",
    "post": "a community post",
}

_COPY_THE_HANDLE = (
    "open it and paste the @handle from the channel page instead"
)


@dataclass(frozen=True, slots=True)
class ChannelIdLookup:
    channel_id: str


@dataclass(frozen=True, slots=True)
class HandleLookup:
    """A handle without its ``@``, lowercased: handles compare that way."""

    handle: str


@dataclass(frozen=True, slots=True)
class LegacyNameLookup:
    """A legacy ``/user/`` name, lowercased."""

    name: str


Lookup = ChannelIdLookup | HandleLookup | LegacyNameLookup


@dataclass(frozen=True, slots=True)
class Entry:
    """One line of input, and the lookup it became."""

    where: str
    text: str
    lookup: Lookup


def _handle(text: str) -> HandleLookup:
    """A handle as given, checked only for what can never be one.

    YouTube's own rules for handles are not in the API reference, so the
    API is left to judge the rest; this catches pasted sentences and
    broken links, not near-misses.
    """
    if not text:
        raise ValueError("an empty handle")
    if any(char.isspace() for char in text) or any(c in text for c in "/?#@"):
        raise ValueError(
            "not a handle: a handle has no spaces and no / ? # @ in it"
        )
    return HandleLookup(text.lower())


def _from_link(text: str) -> Lookup:
    url = urlsplit(text if "://" in text else f"https://{text}")
    host = (url.hostname or "").lower()
    if host in {"youtu.be", "www.youtu.be"}:
        raise ValueError(f"a link to a video — {_COPY_THE_HANDLE}")
    if host not in _HOSTS:
        raise ValueError("not a youtube.com link")

    segments = [unquote(part) for part in url.path.split("/") if part]
    if not segments:
        raise ValueError(
            f"a link to youtube.com itself, not a channel — {_COPY_THE_HANDLE}"
        )
    first = segments[0]

    if first.startswith("@"):
        return _handle(first[1:])
    if first == "channel":
        if len(segments) > 1 and CHANNEL_ID.match(segments[1]):
            return ChannelIdLookup(segments[1])
        raise ValueError("a /channel/ link without a channel id in it")
    if first == "user":
        if len(segments) > 1 and segments[1]:
            return LegacyNameLookup(segments[1].lower())
        raise ValueError("a /user/ link without a name in it")
    if first == "c":
        raise ValueError(
            "a /c/ custom URL — the API cannot look these up; "
            f"{_COPY_THE_HANDLE}"
        )
    if first in _NOT_A_CHANNEL:
        raise ValueError(
            f"a link to {_NOT_A_CHANNEL[first]}, not a channel — "
            f"{_COPY_THE_HANDLE}"
        )
    raise ValueError(
        f"a youtube.com/{first} link — the API cannot look these up; "
        f"{_COPY_THE_HANDLE}"
    )


def _parse_one(entry: str) -> Lookup:
    """One entry as a lookup. Raises ``ValueError`` with the reason."""
    text = entry.strip()
    if not text:
        raise ValueError("empty")
    if CHANNEL_ID.match(text):
        return ChannelIdLookup(text)
    if text.startswith("@"):
        return _handle(text[1:])
    # A dot alone does not make a link: handles may contain one.
    if "/" in text or text.lower().startswith(_LINK_STARTS):
        return _from_link(text)
    # A bare word is a handle, never a legacy name: legacy names come
    # only from /user/ links, and guessing would spend a unit to find out.
    return _handle(text)


def _collect(labelled: Iterable[tuple[str, str]]) -> list[Entry]:
    """Parse every entry, then raise once if any failed.

    Entries that come to the same lookup — the same handle in another
    form or case — collapse into the first, so they cost one request
    between them. First-seen order is kept, so a run bounded by
    ``--limit`` covers the same entries in the same order when repeated.
    """
    entries: dict[Lookup, Entry] = {}
    invalid: list[InvalidEntry] = []
    for where, text in labelled:
        try:
            lookup = _parse_one(text)
        except ValueError as exc:
            invalid.append(InvalidEntry(where, text.strip(), str(exc)))
            continue
        entries.setdefault(lookup, Entry(where, text.strip(), lookup))
    if invalid:
        raise EntryError(invalid)
    return list(entries.values())


def parse_entries(entries: Iterable[str]) -> list[Entry]:
    """Channels given as arguments, in the forms a person pastes."""
    return _collect(
        (f"argument {position}", entry)
        for position, entry in enumerate(entries, start=1)
    )


def read_entries(text: str) -> list[Entry]:
    """Channels from a file's text: one per line, ``#`` comments skipped.

    A comment may fill a line or follow an entry after whitespace — which
    is the form the failures file is written in, so it reads back as the
    next run's input. A ``#`` with no space before it is part of the
    entry: a URL fragment, never a comment.
    """
    entries = (
        (f"line {number}", _TRAILING_COMMENT.split(line, maxsplit=1)[0])
        for number, line in enumerate(text.splitlines(), start=1)
    )
    return _collect(
        (where, entry)
        for where, entry in entries
        if entry.strip() and not entry.lstrip().startswith("#")
    )
