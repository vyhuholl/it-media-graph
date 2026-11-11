"""What a YouTube video's title and description reference.

Pure: text and plain mappings in, small dataclasses out. No network, no
session. Three steps, each testable alone. :func:`read_text` turns one
text into the forms it names — handles, channel ids, legacy names, video
ids, bare mentions — and counts what it saw and will not follow.
:func:`resolve` turns every source video's forms, against what the
inventory already knows, into edges and pending targets. :func:`report`
turns that reading into the lines a person reads, ordered by
:func:`ranked` — by how many distinct seeds name a target, the only
number worked out per target.

Nothing here is stored, by anyone. A handle read out of a description
lives only in its payload, so the graph is worked out from the payloads
each time it is asked for and always reflects them.

Two handle readers, and they are not interchangeable. A handle *link* is
an open set: an unknown one becomes a pending target and, in change 5, a
unit of quota — so it is held to YouTube's grammar. A bare ``@handle``
in text is matched against the closed set of handles the inventory
carries and nothing else: measured on the seeds, it named a known
handle 5–47 times less often than a link did, and most of what it names
are people's accounts elsewhere. The rule is ``affiliation/signals.py``'s
— a loose reader is safe against a closed set and nowhere else.
Telegram's ``derive/references.py`` is no use here either way: its
grammar has no dot and no hyphen, and would read ``@fake.seed.two`` as
``fake``, which is somebody else.

:func:`read_link` — one URL into what it names — is public because the
Telegram seeds' links are read by the same grammar
(``derive/tg_youtube.py``): there the URL comes from an entity, already
cut out of its sentence.
"""

import re
import unicodedata
from collections import Counter
from collections.abc import Callable, Iterable, Mapping, Sequence
from dataclasses import dataclass, field
from datetime import datetime
from enum import StrEnum
from urllib.parse import parse_qs, unquote, urlsplit

__all__ = [
    "SHORTENERS",
    "Edge",
    "Evidence",
    "Form",
    "Known",
    "Link",
    "LinkKind",
    "Named",
    "NotFollowed",
    "Pending",
    "PendingKind",
    "References",
    "Source",
    "normalize_handle",
    "ranked",
    "read_link",
    "read_text",
    "report",
    "resolve",
]


# A handle's length, by YouTube's rule for most scripts. Han and Hangul
# allow 1–10 and kana 2–20; one rule for all refuses those short ones,
# and none occurs in the inventory.
_HANDLE_MIN = 3
_HANDLE_MAX = 30
# What YouTube allows inside a handle besides letters and digits, and
# never at either end — so at the end of a match they are punctuation.
_SEPARATORS = "_-.·"

_CHANNEL_ID = re.compile(r"^UC[0-9A-Za-z_-]{22}$")
_VIDEO_ID = re.compile(r"^[0-9A-Za-z_-]{11}$")

_YOUTUBE_HOSTS = frozenset(
    {"youtube.com", "www.youtube.com", "m.youtube.com", "music.youtube.com"}
)
_NOCOOKIE_HOSTS = frozenset(
    {"youtube-nocookie.com", "www.youtube-nocookie.com"}
)
_SHORT_HOSTS = frozenset({"youtu.be", "www.youtu.be"})

# A link to YouTube in running text. The look-behind keeps a host that
# merely ends in these letters — `notyoutube.com` — and an e-mail domain
# out; brackets and quotes end a link because they end it in prose.
_YOUTUBE_LINK = re.compile(
    r"(?<![\w.@-])(?:https?://)?(?:(?:www|m|music)\.)?"
    r"(?:youtube\.com|youtube-nocookie\.com|youtu\.be)"
    r"(?:/[^\s<>\"'(){}\[\]]*)?",
    re.IGNORECASE,
)

# What a link picks up from the sentence around it.
_TRAILING = ".,;:!?…»\"'"

# First path segments of youtube.com that are neither a channel nor a
# video: pages of YouTube itself. Anything else in that place is a
# vanity URL — `youtube.com/<name>` — which the API cannot look up.
_YOUTUBE_PAGES = frozenset(
    {
        "about",
        "account",
        "attribution_link",
        "clip",
        "creators",
        "feed",
        "gaming",
        "hashtag",
        "howyoutubeworks",
        "kids",
        "live_chat",
        "music",
        "playlists",
        "post",
        "premium",
        "redirect",
        "results",
        "s",
        "signin",
        "subscription_center",
        "t",
        "trends",
        "upload",
        "watch_videos",
        "yt",
    }
)

# Link shorteners: a link through one is counted and never expanded —
# expanding it is a request to someone other than YouTube.
SHORTENERS = frozenset(
    {
        "amzn.to",
        "apple.co",
        "bit.do",
        "bit.ly",
        "buff.ly",
        "clck.ru",
        "cutt.ly",
        "dub.sh",
        "geni.us",
        "goo.gl",
        "goo.gle",
        "hubs.la",
        "hubs.ly",
        "is.gd",
        "jb.gg",
        "lnkd.in",
        "ow.ly",
        "rb.gy",
        "rebrand.ly",
        "s.id",
        "shorturl.at",
        "spoti.fi",
        "surl.li",
        "t.co",
        "tiny.cc",
        "tinyurl.com",
    }
)
_SHORTENED = re.compile(
    r"(?<![\w.@-])(?:https?://)?(?:www\.)?(?:"
    + "|".join(re.escape(host) for host in sorted(SHORTENERS))
    + r")/\S",
    re.IGNORECASE,
)
# Counted by number only: finding Telegram channels through YouTube data
# is a purpose the site does not state, so not even the name is kept.
_TELEGRAM = re.compile(
    r"(?<![\w.@-])(?:https?://)?(?:www\.)?(?:t|telegram)\.(?:me|dog)/\S",
    re.IGNORECASE,
)

# A bare `@` that may open a mention: not inside a word, an e-mail
# address or a URL's path.
_AT = re.compile(r"(?<![\w.%+/@-])@")


class Form(StrEnum):
    """How a reference was written. The order is the precedence.

    ``FEATURED`` is a seed's featured list naming a channel: a reference
    of the channel, not of a video, so it never competes with the others
    for a video's one reference to a target.
    """

    ID_LINK = "id link"
    HANDLE_LINK = "handle link"
    MENTION = "mention"
    VIDEO_LINK = "video link"
    FEATURED = "featured"


_PRECEDENCE = {form: rank for rank, form in enumerate(Form)}


class NotFollowed(StrEnum):
    """What is counted and never turned into a reference."""

    CUSTOM_URL = "custom URLs"
    PLAYLIST = "playlist pages"
    SHORTENED = "shortened links"
    TELEGRAM = "Telegram links"
    UNKNOWN_MENTION = "mentions outside the inventory"


class LinkKind(StrEnum):
    """What one YouTube link names, or what it is counted as."""

    CHANNEL = "channel"
    HANDLE = "handle"
    LEGACY = "legacy"
    VIDEO = "video"
    CUSTOM_URL = "custom URL"
    PLAYLIST = "playlist"


@dataclass(frozen=True, slots=True)
class Link:
    """One YouTube link, as :func:`read_link` reads it.

    ``value`` is the channel or video id with its case kept, the
    normalized handle, the lowercased legacy name — or, for a custom
    URL, its name, NFC and lowercased: a name to show a person, not
    something the API can look up. Empty for a playlist page.
    """

    kind: LinkKind
    value: str


class PendingKind(StrEnum):
    """What a pending target is, and so how change 5 will look it up."""

    HANDLE = "handle"
    LEGACY = "legacy"
    CHANNEL = "id"
    VIDEO = "video"


@dataclass(frozen=True, slots=True)
class Named:
    """What one text names, before anything is looked up.

    Handles and legacy names are normalized: NFC, lowercased, a handle
    without its ``@``. Ids keep their case — YouTube's ids are
    case-sensitive.
    """

    channel_ids: tuple[str, ...] = ()
    handles: tuple[str, ...] = ()
    legacy_names: tuple[str, ...] = ()
    video_ids: tuple[str, ...] = ()
    mentions: tuple[str, ...] = ()
    not_followed: Mapping[NotFollowed, int] = field(default_factory=dict)


@dataclass(frozen=True, slots=True)
class Source:
    """One seed's video, and what its title and description name."""

    video_id: str
    channel_id: str
    published_at: datetime | None
    named: Named


@dataclass(frozen=True, slots=True)
class Known:
    """What the inventory and the stored videos answer without a request.

    ``handles`` maps each handle a stored payload carries to every
    channel carrying it — more than one is an ambiguity, not a choice.
    ``videos`` maps each stored video to its channel.
    """

    handles: Mapping[str, frozenset[str]]
    channels: frozenset[str]
    videos: Mapping[str, str | None]


@dataclass(frozen=True, slots=True, order=True)
class Pending:
    """A target nothing stored can name: the key change 5 looks up."""

    kind: PendingKind
    value: str


@dataclass(slots=True)
class Evidence:
    """Which seeds, and which of their videos, reference one target.

    ``seeds`` is every seed naming it, however; ``linking`` the seeds
    whose videos do, and ``featuring`` those whose featured lists do —
    kept apart because a review queue may count one and not the other.
    """

    seeds: set[str] = field(default_factory=set)
    videos: set[str] = field(default_factory=set)
    linking: set[str] = field(default_factory=set)
    featuring: set[str] = field(default_factory=set)


@dataclass(frozen=True, slots=True)
class Edge:
    """One reference from a seed to a known channel or video.

    ``video_id`` is the referencing video, or ``None`` for a reference
    from the seed's featured list, which no video makes.
    """

    video_id: str | None
    channel_id: str
    published_at: datetime | None
    target_channel: str
    target_video: str | None
    form: Form


@dataclass(slots=True)
class References:
    """Everything the seeds' videos reference, as of one reading.

    ``handles`` is each known channel's handle, for a person to read in
    the report; like everything here, it lasts as long as the reading.
    """

    videos: int = 0
    seeds: int = 0
    featured_lists: int = 0
    edges: list[Edge] = field(default_factory=list)
    pending: dict[Pending, Evidence] = field(default_factory=dict)
    ambiguous: dict[str, frozenset[str]] = field(default_factory=dict)
    not_followed: Counter[NotFollowed] = field(default_factory=Counter)
    handles: dict[str, str] = field(default_factory=dict)

    def known_targets(self) -> dict[str, frozenset[str]]:
        """Each known target channel, and the distinct seeds naming it."""
        seeds: dict[str, set[str]] = {}
        for edge in self.edges:
            seeds.setdefault(edge.target_channel, set()).add(edge.channel_id)
        return {target: frozenset(by) for target, by in seeds.items()}


def _handle_char(char: str) -> bool:
    """A letter, a digit or a combining mark, of any script.

    Marks are why this is not ``\\w``: Python's ``\\w`` stops at a
    Devanagari vowel sign, and a handle in Hindi is a handle.
    """
    return char.isalnum() or unicodedata.category(char).startswith("M")


def normalize_handle(raw: str) -> str | None:
    """A handle as it is compared, or ``None`` if it cannot be one.

    NFC, no ``@``, separators at the end dropped as punctuation, then
    YouTube's rule: letters and digits of any script with ``_ - . ·``
    inside, three to thirty characters, lowercased — "Handles aren't
    case-sensitive", and every ``customUrl`` measured is lowercase.
    """
    handle = unicodedata.normalize("NFC", raw).removeprefix("@")
    handle = handle.rstrip(_SEPARATORS)
    if not _HANDLE_MIN <= len(handle) <= _HANDLE_MAX:
        return None
    if not handle[0].isalnum():
        return None
    if not all(_handle_char(c) or c in _SEPARATORS for c in handle):
        return None
    return handle.lower()


def _custom_name(raw: str) -> str:
    return unicodedata.normalize("NFC", raw).lower()


def read_link(raw: str) -> Link | None:
    """One YouTube link as what it names or what it counts as, or nothing.

    A channel id, handle, legacy-name or video link names that channel
    or video; a custom URL (``/c/NAME`` or ``youtube.com/NAME``) and a
    playlist page are returned to be counted; YouTube's own pages, a
    link too broken to read and any other host are ``None``. The URL is
    taken as it is: trailing punctuation is the caller's to strip.
    """
    parts = urlsplit(raw if "://" in raw else f"https://{raw}")
    host = (parts.hostname or "").lower()
    segments = [unquote(part) for part in parts.path.split("/") if part]
    first = segments[0] if segments else ""
    second = segments[1] if len(segments) > 1 else ""

    if host in _SHORT_HOSTS:
        return Link(LinkKind.VIDEO, first) if _VIDEO_ID.match(first) else None
    if host in _NOCOOKIE_HOSTS:
        if first.lower() in {"embed", "v"} and _VIDEO_ID.match(second):
            return Link(LinkKind.VIDEO, second)
        return None
    if host not in _YOUTUBE_HOSTS or not first:
        return None

    lowered = first.lower()
    if first.startswith("@"):
        handle = normalize_handle(first)
        return Link(LinkKind.HANDLE, handle) if handle else None
    if lowered == "channel":
        if _CHANNEL_ID.match(second):
            return Link(LinkKind.CHANNEL, second)
        return None
    if lowered == "user":
        name = unicodedata.normalize("NFC", second).lower()
        return Link(LinkKind.LEGACY, name) if name else None
    if lowered == "watch":
        found = parse_qs(parts.query).get("v", [])
        video = found[0] if found else ""
        return Link(LinkKind.VIDEO, video) if _VIDEO_ID.match(video) else None
    if lowered in {"shorts", "live", "embed", "v"}:
        if _VIDEO_ID.match(second):
            return Link(LinkKind.VIDEO, second)
        return None
    if lowered == "playlist":
        return Link(LinkKind.PLAYLIST, "")
    if lowered == "c":
        return (
            Link(LinkKind.CUSTOM_URL, _custom_name(second)) if second else None
        )
    if lowered in _YOUTUBE_PAGES:
        return None
    return Link(LinkKind.CUSTOM_URL, _custom_name(first))


def _mentions(text: str) -> list[str]:
    """Every bare ``@handle`` in the text, normalized, in order seen.

    A run of handle characters after an ``@`` that does not follow a
    word, an e-mail's local part or a URL path — and is not followed by
    ``/``, which makes it a package scope (``@scope/package``).
    """
    found: list[str] = []
    for match in _AT.finditer(text):
        end = match.end()
        while end < len(text) and (
            _handle_char(text[end]) or text[end] in _SEPARATORS
        ):
            end += 1
        if text[end : end + 1] == "/":
            continue
        handle = normalize_handle(text[match.end() : end])
        if handle is not None:
            found.append(handle)
    return found


def read_text(text: str) -> Named:
    """What one text names: links followed, links counted, bare mentions."""
    text = unicodedata.normalize("NFC", text)
    not_followed: Counter[NotFollowed] = Counter()
    found: dict[str, list[str]] = {
        "channel": [],
        "handle": [],
        "legacy": [],
        "video": [],
    }
    for match in _YOUTUBE_LINK.finditer(text):
        link = read_link(match.group(0).rstrip(_TRAILING))
        if link is None:
            continue
        if link.kind is LinkKind.CUSTOM_URL:
            # Counted by number: here the name is API data.
            not_followed[NotFollowed.CUSTOM_URL] += 1
        elif link.kind is LinkKind.PLAYLIST:
            not_followed[NotFollowed.PLAYLIST] += 1
        else:
            found[link.kind.value].append(link.value)

    not_followed[NotFollowed.SHORTENED] += len(_SHORTENED.findall(text))
    not_followed[NotFollowed.TELEGRAM] += len(_TELEGRAM.findall(text))

    return Named(
        channel_ids=tuple(found["channel"]),
        handles=tuple(found["handle"]),
        legacy_names=tuple(found["legacy"]),
        video_ids=tuple(found["video"]),
        mentions=tuple(_mentions(text)),
        not_followed={kind: n for kind, n in not_followed.items() if n},
    )


def resolve(
    sources: Iterable[Source],
    known: Known,
    featured: Mapping[str, Sequence[str]] | None = None,
) -> References:
    """Every source's forms, against what is known, as edges and pending.

    Per video: one reference per target channel and target video, a
    channel reached two ways recorded once in the first form of
    :class:`Form`; a reference to the video's own channel, or to another
    video of it, dropped. A handle two channels carry names neither.

    ``featured`` maps a seed to the channel ids its featured list names:
    each one reference of the seed, of the form ``FEATURED`` and with no
    video — an edge when known, a pending channel id otherwise — and
    none to the seed's own channel.
    """
    references = References(
        handles={
            channel: handle
            for handle, channels in sorted(known.handles.items())
            for channel in channels
        }
    )
    seeds: set[str] = set()
    for source in sources:
        references.videos += 1
        seeds.add(source.channel_id)
        _resolve_one(source, known, references)
    for seed, channels in sorted((featured or {}).items()):
        references.featured_lists += 1
        seeds.add(seed)
        _resolve_featured(seed, channels, known, references)
    references.seeds = len(seeds)
    return references


def _resolve_featured(
    seed: str, channels: Sequence[str], known: Known, into: References
) -> None:
    for channel in sorted(dict.fromkeys(channels)):
        if channel == seed:
            continue
        if channel in known.channels:
            into.edges.append(
                Edge(
                    video_id=None,
                    channel_id=seed,
                    published_at=None,
                    target_channel=channel,
                    target_video=None,
                    form=Form.FEATURED,
                )
            )
            continue
        evidence = into.pending.setdefault(
            Pending(PendingKind.CHANNEL, channel), Evidence()
        )
        evidence.seeds.add(seed)
        evidence.featuring.add(seed)


def _resolve_one(source: Source, known: Known, into: References) -> None:
    named = source.named
    into.not_followed.update(named.not_followed)
    # (target channel, target video) -> form; the precedence picks.
    found: dict[tuple[str, str | None], Form] = {}

    def edge(channel: str, video: str | None, form: Form) -> None:
        if channel == source.channel_id:
            return
        key = (channel, video)
        if key not in found or _PRECEDENCE[form] < _PRECEDENCE[found[key]]:
            found[key] = form

    def pending(kind: PendingKind, value: str) -> None:
        evidence = into.pending.setdefault(Pending(kind, value), Evidence())
        evidence.seeds.add(source.channel_id)
        evidence.linking.add(source.channel_id)
        evidence.videos.add(source.video_id)

    def by_handle(handle: str) -> str | None:
        """The one channel carrying the handle; records an ambiguity."""
        carriers = known.handles.get(handle, frozenset())
        if source.channel_id in carriers:
            return source.channel_id
        if len(carriers) > 1:
            into.ambiguous[handle] = carriers
            return None
        return next(iter(carriers), None)

    for channel in named.channel_ids:
        if channel in known.channels:
            edge(channel, None, Form.ID_LINK)
        else:
            pending(PendingKind.CHANNEL, channel)
    for handle in named.handles:
        if handle not in known.handles:
            pending(PendingKind.HANDLE, handle)
        elif (target := by_handle(handle)) is not None:
            edge(target, None, Form.HANDLE_LINK)
    for name in named.legacy_names:
        # No stored payload carries a legacy name, so it is never known.
        pending(PendingKind.LEGACY, name)
    for mention in named.mentions:
        if mention not in known.handles:
            into.not_followed[NotFollowed.UNKNOWN_MENTION] += 1
        elif (target := by_handle(mention)) is not None:
            edge(target, None, Form.MENTION)
    for video in named.video_ids:
        # A stored payload without a channel names no owner either.
        if (owner := known.videos.get(video)) is None:
            pending(PendingKind.VIDEO, video)
        else:
            edge(owner, video, Form.VIDEO_LINK)

    into.edges.extend(
        Edge(
            video_id=source.video_id,
            channel_id=source.channel_id,
            published_at=source.published_at,
            target_channel=channel,
            target_video=video,
            form=form,
        )
        for (channel, video), form in sorted(
            found.items(), key=lambda item: (item[0][0], item[0][1] or "")
        )
    )


def ranked[T](
    targets: Mapping[T, frozenset[str]],
    *,
    key: Callable[[T], str],
    min_seeds: int = 1,
    limit: int | None = None,
) -> list[tuple[T, frozenset[str]]]:
    """Targets with their seeds, most distinct seeds first, then by key.

    The count of distinct seeds is the only number worked out per
    target: no weight, no score, nothing over the graph. ``limit`` of
    ``None`` or zero lifts the bound.
    """
    rows = [
        (target, seeds)
        for target, seeds in targets.items()
        if len(seeds) >= min_seeds
    ]
    rows.sort(key=lambda row: (-len(row[1]), key(row[0])))
    return rows[:limit] if limit else rows


def _seeds_column(seeds: frozenset[str]) -> str:
    count = f"{len(seeds)} seed" + ("" if len(seeds) == 1 else "s")
    return f"{count:<9}  {' '.join(sorted(seeds))}"


def _pending_label(target: Pending) -> str:
    value = (
        f"@{target.value}"
        if target.kind is PendingKind.HANDLE
        else target.value
    )
    return f"{target.kind.value:<7}  {value:<28}"


def report(
    references: References, *, min_seeds: int = 1, limit: int | None = 50
) -> list[str]:
    """The reading as lines a person reads: totals, targets, what was not.

    Known channels, then pending targets, each with the ids of the seeds
    naming it, most seeds first — the order is the only use the count is
    put to. Bounded per list by ``min_seeds`` and ``limit`` (zero or
    ``None`` lifts it), with what the bound left out said in one line.
    Telegram links are a number here and nothing more.
    """
    known = references.known_targets()
    pending = {
        target: frozenset(evidence.seeds)
        for target, evidence in references.pending.items()
    }
    lines = [
        (
            f"read {references.videos} videos and "
            f"{references.featured_lists} featured lists of "
            f"{references.seeds} seeds: {len(references.edges)} references "
            f"to {len(known)} known channels, {len(pending)} pending targets"
        )
    ]

    def listed[T](
        title: str,
        rows: list[tuple[T, frozenset[str]]],
        eligible: int,
        label: Callable[[T], str],
    ) -> None:
        lines.append(f"{title}, most seeds first:")
        lines.extend(
            f"  {label(target)}  {_seeds_column(seeds)}"
            for target, seeds in rows
        )
        if not rows:
            lines.append("  none")
        if eligible > len(rows):
            lines.append(f"  … and {eligible - len(rows)} more")

    def known_label(channel: str) -> str:
        handle = references.handles.get(channel)
        return f"{channel}  {f'@{handle}' if handle else '(no data)':<28}"

    eligible_known = ranked(known, key=str, min_seeds=min_seeds)
    listed(
        "known channels",
        eligible_known[:limit] if limit else eligible_known,
        len(eligible_known),
        known_label,
    )
    eligible_pending = ranked(
        pending, key=lambda t: f"{t.kind} {t.value}", min_seeds=min_seeds
    )
    listed(
        "pending",
        eligible_pending[:limit] if limit else eligible_pending,
        len(eligible_pending),
        _pending_label,
    )

    lines.append(
        "not followed: "
        + ", ".join(
            f"{references.not_followed[kind]} {kind.value}"
            for kind in NotFollowed
        )
    )
    if references.ambiguous:
        lines.append("ambiguous handles:")
        lines.extend(
            f"  @{handle} carried by {' '.join(sorted(channels))}"
            for handle, channels in sorted(references.ambiguous.items())
        )
    else:
        lines.append("ambiguous handles: none")
    return lines
