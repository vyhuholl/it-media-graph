"""Which YouTube channels the Telegram seeds link to, and in what order.

Pure: payloads and plain mappings in, small dataclasses and lines out.
No network, no session, no database. Three steps, each testable alone.
:func:`read_post` turns one Telegram post into the YouTube links it
carries. :func:`name_channels` turns every post's links, against what is
stored — the inventory, stored payloads, lookups in date — into each
YouTube channel's evidence: the distinct seed families linking it.
:func:`ranking` orders the channels outside the inventory by that
evidence, and :func:`report` turns it into the lines a person reads.

A post's links come from three places only — the URLs Telegram marked
in its text, the URLs behind its text, and its link preview's URL — and
each is read by change 4's grammar (:func:`yt_references.read_link`).
Never read: plain text Telegram did not mark, a preview's title,
description, author or site name (YouTube's metadata as Telegram fetched
it, which identifying a channel by would take API-like data outside the
API), and ``@name``, which on Telegram is a username.

Nothing worked out here is stored, by anyone. Which families link a
channel is joined from Telegram's posts and the API's answers each time
it is asked for (``youtube-telegram-links``, "Nothing Joined Is
Stored"). The family count orders a review queue and is the only number
used to: no weight, no score.
"""

from collections import Counter
from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass, field
from datetime import datetime
from enum import StrEnum
from typing import Any
from urllib.parse import parse_qs, urlsplit

from itgraph.derive.references import slice_utf16
from itgraph.derive.yt_references import SHORTENERS, LinkKind, read_link

__all__ = [
    "PREFILTER",
    "Evidence",
    "Known",
    "Name",
    "NameKind",
    "Post",
    "PostLinks",
    "Ranked",
    "Reading",
    "Scanned",
    "is_own",
    "name_channels",
    "post_urls",
    "ranking",
    "read_post",
    "report",
    "to_look_up",
]

# A superset of every post `read_post` can find something in, for the
# loader to filter on in SQL so it does not stream every post of every
# seed: a YouTube host always contains `youtu`, and a shortener is one
# of a known list. Being a superset is what makes it safe; the reader
# here stays the authority, and a test holds the two together.
PREFILTER = "youtu|" + "|".join(
    host.replace(".", r"\.") for host in sorted(SHORTENERS)
)


class NameKind(StrEnum):
    """A linked name the API is asked about. Values match ``YtLookupKind``.

    A channel id is not one: it is its own answer.
    """

    VIDEO = "video"
    HANDLE = "handle"
    LEGACY = "legacy"


@dataclass(frozen=True, slots=True, order=True)
class Name:
    """A video id, a normalized handle or a lowercased legacy name."""

    kind: NameKind
    value: str


@dataclass(frozen=True, slots=True)
class PostLinks:
    """What one post links on YouTube, each name once.

    ``custom_urls`` are names, lowercased: Telegram's text, not API data,
    so they may be shown for a person to look up by hand.
    """

    channel_ids: frozenset[str] = frozenset()
    handles: frozenset[str] = frozenset()
    legacy_names: frozenset[str] = frozenset()
    video_ids: frozenset[str] = frozenset()
    custom_urls: frozenset[str] = frozenset()
    playlists: int = 0
    shortened: int = 0

    def links_youtube(self) -> bool:
        """Whether the post links anything on YouTube, followed or not."""
        return bool(
            self.channel_ids
            or self.handles
            or self.legacy_names
            or self.video_ids
            or self.custom_urls
            or self.playlists
        )


@dataclass(frozen=True, slots=True)
class Post:
    """One seed's own post: whose, which family, when, what it links."""

    channel: int
    family: int
    posted_at: datetime
    links: PostLinks


@dataclass(frozen=True, slots=True)
class Known:
    """What answers a name without a request.

    ``inventory``: every channel with a record, whatever its status.
    ``handles``: each handle a stored channel payload carries, with every
    channel carrying it — two are an ambiguity, and the lookup decides.
    ``videos``: each stored video's channel, ``None`` where its payload
    names none. ``lookups``: each lookup in date, ``None`` for not found.
    """

    inventory: frozenset[str] = frozenset()
    handles: Mapping[str, frozenset[str]] = field(default_factory=dict)
    videos: Mapping[str, str | None] = field(default_factory=dict)
    lookups: Mapping[Name, str | None] = field(default_factory=dict)


@dataclass(slots=True)
class Evidence:
    """Which families link one channel, and when one last did.

    ``written`` is a handle (with ``@``) or a legacy name some post wrote
    for the channel — Telegram's text, never the API's.
    """

    families: set[int] = field(default_factory=set)
    newest: datetime | None = None
    handles: set[str] = field(default_factory=set)
    legacy_names: set[str] = field(default_factory=set)

    @property
    def written(self) -> str | None:
        if self.handles:
            return f"@{min(self.handles)}"
        if self.legacy_names:
            return min(self.legacy_names)
        return None


@dataclass(frozen=True, slots=True)
class Scanned:
    """How many own seed posts were read in all, and from whom.

    The loader streams only posts that can link anything, so the totals
    of what it read come from it rather than from the posts given.
    """

    posts: int
    seeds: int
    families: int


@dataclass(slots=True)
class Reading:
    """Every seed post's links, named as of one reading.

    ``awaiting`` and ``not_found`` map each name to the families linking
    it; ``custom_urls`` each custom URL's name to its families.
    """

    scanned: Scanned = field(default_factory=lambda: Scanned(0, 0, 0))
    linking_posts: int = 0
    linking_families: set[int] = field(default_factory=set)
    channels: dict[str, Evidence] = field(default_factory=dict)
    awaiting: dict[Name, set[int]] = field(default_factory=dict)
    not_found: dict[Name, set[int]] = field(default_factory=dict)
    custom_urls: dict[str, set[int]] = field(default_factory=dict)
    counted: Counter[str] = field(default_factory=Counter)


@dataclass(frozen=True, slots=True)
class Ranked:
    """One channel outside the inventory, in ranking order."""

    channel_id: str
    families: int
    newest: datetime
    written: str | None


def is_own(payload: Mapping[str, Any]) -> bool:
    """Whether the channel wrote the post itself: no forward header.

    A forward is skipped whoever it came from — a channel, a user, a
    hidden sender, the channel itself. The link in it is the author's
    choice, and the forwarder endorsed the post, not the link.
    """
    return payload.get("fwd_from") is None


def post_urls(payload: Mapping[str, Any]) -> list[str]:
    """The URLs a post carries, from the three places read, in order.

    A ``MessageEntityUrl`` is sliced out of the text in UTF-16 units —
    Telegram marked its bounds, so no trailing punctuation rides along.
    A ``MessageEntityTextUrl`` carries its URL. A preview carries one
    when it has any: a pending or empty preview may not.
    """
    text = payload.get("message") or ""
    urls: list[str] = []
    for entity in payload.get("entities") or ():
        kind = entity.get("_")
        if kind == "MessageEntityUrl":
            urls.append(
                slice_utf16(
                    text, entity.get("offset", 0), entity.get("length", 0)
                )
            )
        elif kind == "MessageEntityTextUrl" and entity.get("url"):
            urls.append(str(entity["url"]))
    media = payload.get("media")
    webpage = media.get("webpage") if isinstance(media, dict) else None
    if isinstance(webpage, dict) and isinstance(webpage.get("url"), str):
        urls.append(webpage["url"])
    return urls


def _host(url: str) -> str:
    parts = urlsplit(url if "://" in url else f"https://{url}")
    return (parts.hostname or "").lower().removeprefix("www.")


def _playlist_key(url: str) -> str:
    """One playlist however its link is written: its ``list`` id."""
    parts = urlsplit(url if "://" in url else f"https://{url}")
    found = parse_qs(parts.query).get("list", [])
    return found[0] if found else url


def read_post(payload: Mapping[str, Any]) -> PostLinks:
    """What one post links on YouTube, read from its three places.

    Each name once per post: a link in the text and the same link in the
    preview are one link. Playlists are told apart by their ``list`` id,
    shortened links by their text.
    """
    found: dict[LinkKind, set[str]] = {kind: set() for kind in LinkKind}
    shortened: set[str] = set()
    for url in post_urls(payload):
        link = read_link(url)
        if link is None:
            if _host(url) in SHORTENERS:
                shortened.add(url)
            continue
        if link.kind is LinkKind.PLAYLIST:
            found[link.kind].add(_playlist_key(url))
        else:
            found[link.kind].add(link.value)
    return PostLinks(
        channel_ids=frozenset(found[LinkKind.CHANNEL]),
        handles=frozenset(found[LinkKind.HANDLE]),
        legacy_names=frozenset(found[LinkKind.LEGACY]),
        video_ids=frozenset(found[LinkKind.VIDEO]),
        custom_urls=frozenset(found[LinkKind.CUSTOM_URL]),
        playlists=len(found[LinkKind.PLAYLIST]),
        shortened=len(shortened),
    )


def _answer(name: Name, known: Known) -> tuple[bool, str | None]:
    """Whether the name is answered without a request, and by what.

    ``(True, id)`` names a channel, ``(True, None)`` was looked up and
    named none, ``(False, None)`` awaits a lookup.
    """
    if name.kind is NameKind.VIDEO:
        owner = known.videos.get(name.value)
        if owner is not None:
            return True, owner
    elif name.kind is NameKind.HANDLE:
        carriers = known.handles.get(name.value, frozenset())
        if len(carriers) == 1:
            return True, next(iter(carriers))
    if name in known.lookups:
        return True, known.lookups[name]
    return False, None


def _credit(
    named: dict[str, tuple[set[str], set[str]]],
    channel: str,
    name: Name | None = None,
) -> None:
    """Note a channel one post links, and the name Telegram wrote for it."""
    handles, legacies = named.setdefault(channel, (set(), set()))
    if name is not None and name.kind is NameKind.HANDLE:
        handles.add(name.value)
    elif name is not None and name.kind is NameKind.LEGACY:
        legacies.add(name.value)


def _read_one(post: Post, known: Known, into: Reading) -> None:
    links = post.links
    into.counted["playlist pages"] += links.playlists
    into.counted["shortened links"] += links.shortened
    if not links.links_youtube():
        return
    into.linking_posts += 1
    into.linking_families.add(post.family)

    # channel id -> the handles and legacy names this post wrote for it
    named: dict[str, tuple[set[str], set[str]]] = {}
    for channel_id in links.channel_ids:
        _credit(named, channel_id)
    names = [
        *(Name(NameKind.HANDLE, value) for value in links.handles),
        *(Name(NameKind.LEGACY, value) for value in links.legacy_names),
        *(Name(NameKind.VIDEO, value) for value in links.video_ids),
    ]
    for name in names:
        answered, owner = _answer(name, known)
        if not answered:
            into.awaiting.setdefault(name, set()).add(post.family)
        elif owner is None:
            into.not_found.setdefault(name, set()).add(post.family)
        else:
            _credit(named, owner, name)
    for custom in links.custom_urls:
        into.custom_urls.setdefault(custom, set()).add(post.family)

    for channel, (handles, legacies) in named.items():
        evidence = into.channels.setdefault(channel, Evidence())
        evidence.families.add(post.family)
        if evidence.newest is None or post.posted_at > evidence.newest:
            evidence.newest = post.posted_at
        evidence.handles |= handles
        evidence.legacy_names |= legacies


def name_channels(
    posts: Iterable[Post], known: Known, *, scanned: Scanned | None = None
) -> Reading:
    """Every post's links named: each channel's families, and what is not.

    ``scanned`` is what the loader read in all, where it streamed only
    the posts that could link anything; without it the totals are of
    the posts given.
    """
    reading = Reading()
    given = 0
    seeds: set[int] = set()
    families: set[int] = set()
    for post in posts:
        given += 1
        seeds.add(post.channel)
        families.add(post.family)
        _read_one(post, known, reading)
    reading.scanned = scanned or Scanned(given, len(seeds), len(families))
    return reading


def ranking(
    reading: Reading,
    inventory: frozenset[str],
    *,
    min_families: int = 2,
) -> list[Ranked]:
    """The channels outside the inventory, most families first.

    Then the newest post linking each, newest first — Telegram's data,
    and the recency the plan puts in candidate scoring — then the id, so
    a tie is ordered the same way every time. A channel with a record in
    any status, gone included, is never ranked.
    """
    rows: list[Ranked] = []
    for channel, evidence in reading.channels.items():
        if channel in inventory or evidence.newest is None:
            continue
        if len(evidence.families) < min_families:
            continue
        rows.append(
            Ranked(
                channel_id=channel,
                families=len(evidence.families),
                newest=evidence.newest,
                written=evidence.written,
            )
        )
    rows.sort(
        key=lambda row: (
            -row.families,
            -row.newest.timestamp(),
            row.channel_id,
        )
    )
    return rows


def to_look_up(reading: Reading) -> list[Name]:
    """The names awaiting a lookup, best evidenced first.

    Most distinct families first, then by kind and name — so a run
    bounded by its request limit spends on what matters most, and the
    same run over the same data asks in the same order.
    """
    return sorted(
        reading.awaiting,
        key=lambda name: (-len(reading.awaiting[name]), name.kind, name.value),
    )


def _families(count: int) -> str:
    return f"{count} famil" + ("y" if count == 1 else "ies")


def _rest(rows: Sequence[Ranked]) -> str:
    by_count = Counter(row.families for row in rows)
    spread = ", ".join(
        f"{by_count[count]} with {_families(count)}"
        for count in sorted(by_count, reverse=True)
    )
    return f"  … and {len(rows)} more: {spread}"


def report(
    reading: Reading,
    ranked: Sequence[Ranked],
    *,
    top: int,
    inventory: frozenset[str],
) -> list[str]:
    """The reading as lines a person reads. Nothing in it is API data.

    Totals; the top ``top`` ranked channels with the handle Telegram
    wrote for each, the family count and the newest link's date; the
    rest in one line; then what could not be counted.
    """
    known_linked = sum(
        1 for channel in reading.channels if channel in inventory
    )
    lines = [
        (
            f"read {reading.scanned.posts} own posts of "
            f"{reading.scanned.seeds} seeds in "
            f"{_families(reading.scanned.families)}: "
            f"{reading.linking_posts} link YouTube, from "
            f"{_families(len(reading.linking_families))}"
        ),
        f"linked channels already in the inventory: {known_linked}",
        f"ranked, most families first ({len(ranked)} in all):",
    ]
    shown = ranked[:top] if top else ranked
    for position, row in enumerate(shown, start=1):
        lines.append(
            f"  {position:>4}  {row.channel_id}  {row.written or '-':<30}  "
            f"{_families(row.families):<12}  {row.newest.date().isoformat()}"
        )
    if not shown:
        lines.append("  none")
    if len(ranked) > len(shown):
        lines.append(_rest(ranked[len(shown) :]))

    lines.append(
        f"awaiting a lookup: {len(reading.awaiting)} links"
        + (" — run `itgraph yt tg-lookup`" if reading.awaiting else "")
    )
    lines.append(f"looked up, no channel: {len(reading.not_found)} links")
    lines.append("custom URLs, for a person to look up:")
    customs = sorted(
        reading.custom_urls.items(), key=lambda item: (-len(item[1]), item[0])
    )
    lines.extend(f"  {name:<30}  {_families(len(by))}" for name, by in customs)
    if not customs:
        lines.append("  none")
    lines.append(
        f"counted: {reading.counted['playlist pages']} playlist pages, "
        f"{reading.counted['shortened links']} shortened links"
    )
    return lines
