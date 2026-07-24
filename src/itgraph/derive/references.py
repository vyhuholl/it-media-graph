"""Reading references out of a stored message payload.

Pure functions over the JSON the raw layer holds: no database, no
network, no Telethon object. Each one turns a payload — or a fragment of
one — into the channel it points at, and this is where the whole
derivation earns or loses its correctness. The pass that calls these is
mechanical; the branching that decides *what* a message references lives
here, one shape at a time, so it can be tested one shape at a time.

Two references leave here: an id (from a forward header or a
``t.me/c/<id>`` link) and a username (from an ``@mention`` or a
``t.me/name`` link). An id is a channel's primary key and can become an
edge at once; a username needs resolving first. Everything else — a user,
a bot, an invite, a non-Telegram link — resolves to nothing and produces
no reference. Where a link or a forward header names a specific *post*,
the referenced message id — and, for a forward, its date — travels with
the reference, so the edge can point at the post rather than the channel.
"""

import re
from dataclasses import dataclass
from datetime import datetime
from typing import Any
from urllib.parse import urlsplit

__all__ = [
    "Forward",
    "Reference",
    "extract_references",
    "forward_target",
    "normalize_username",
    "parse_tme_link",
    "peer_channel_id",
]

# The hosts a public Telegram link uses. `telegram.dog` is a real alias
# Telegram itself serves, so it is honoured rather than dropped.
_TME_HOSTS = {"t.me", "telegram.me", "telegram.dog"}

# A Telegram username after normalization: 4-32 of [a-z0-9_], starting
# with a letter. The leading-letter rule is what rejects a stray path
# fragment, and the length floor is what rejects the reserved single
# letters `s` and `c` should they ever reach here as a bare segment.
_USERNAME = re.compile(r"^[a-z][a-z0-9_]{3,31}$")


@dataclass(frozen=True, slots=True)
class Reference:
    """A channel a message points at, by exactly one of two handles.

    ``username`` is set for an ``@mention`` or a ``t.me/name`` link;
    ``channel_id`` for a ``t.me/c/<id>`` link. Exactly one, never both.

    ``msg_id`` is the referenced post within that channel when the link
    names one — ``t.me/name/123`` or ``t.me/c/<id>/<msg>`` — and ``None``
    for a mention or a link that addresses the channel as a whole.
    """

    username: str | None = None
    channel_id: int | None = None
    msg_id: int | None = None


@dataclass(frozen=True, slots=True)
class Forward:
    """A channel a message was forwarded from, and the post it names.

    ``channel_id`` is always present: a header naming no channel is not a
    channel-to-channel forward and yields ``None`` from
    :func:`forward_target`, never a ``Forward``. ``msg_id`` and
    ``published_at`` are the original post's id (``fwd_from.channel_post``)
    and date (``fwd_from.date``); a header naming the channel but not the
    post leaves them empty.

    ``published_at`` is the *original* post's date, not that of the hop it
    arrived through: when a forward is itself forwarded Telegram keeps
    ``fwd_from`` pointing at the root author, so the interval derived from
    it is time since original publication.
    """

    channel_id: int
    msg_id: int | None = None
    published_at: datetime | None = None


def normalize_username(raw: str) -> str | None:
    """Lowercase, drop a leading ``@``, validate. ``None`` if implausible.

    Every username crosses this boundary — from a mention entity, from a
    link path — so a lookup and a ``pending_mentions`` key computed from
    the same handle always agree. A value that is not a plausible username
    is dropped rather than stored: a bad key resolves to nothing and only
    wastes a request.
    """
    candidate = raw.strip().removeprefix("@").lower()
    return candidate if _USERNAME.fullmatch(candidate) else None


def peer_channel_id(from_id: Any) -> int | None:
    """The channel id a ``Peer`` names, or ``None`` if it names no channel.

    ``PeerChannel`` carries a ``channel_id`` — a bare id, the primary key
    of ``channels``. ``PeerUser`` and ``PeerChat`` name a person or a
    legacy group and have no place in a channel-to-channel graph. A
    missing peer — a forward whose origin the author's privacy settings
    withhold — is likewise nothing.
    """
    if not isinstance(from_id, dict) or from_id.get("_") != "PeerChannel":
        return None
    channel_id = from_id.get("channel_id")
    return channel_id if isinstance(channel_id, int) else None


def forward_target(
    payload: dict[str, Any], *, src_channel_id: int
) -> Forward | None:
    """The channel a message was forwarded from, and the post it names.

    ``None`` for a message that is not a forward, one forwarded from a
    user, one whose origin is hidden, and one a channel forwarded from
    itself — a self-repost is not a relationship between two channels. A
    forward naming no original post still yields a ``Forward`` — with
    ``msg_id`` and ``published_at`` empty — never ``None``.

    The origin is ``fwd_from.from_id``, the original author.
    ``saved_from_peer`` — the intermediate place a message was copied
    from — is deliberately ignored: it describes the path a forward
    travelled, not who talks to whom, and is re-derivable from the same
    payload if forward chains ever matter.
    """
    fwd = payload.get("fwd_from")
    if not isinstance(fwd, dict):
        return None
    channel_id = peer_channel_id(fwd.get("from_id"))
    if channel_id is None or channel_id == src_channel_id:
        return None
    channel_post = fwd.get("channel_post")
    return Forward(
        channel_id=channel_id,
        msg_id=channel_post if isinstance(channel_post, int) else None,
        published_at=_as_datetime(fwd.get("date")),
    )


def parse_tme_link(url: str) -> Reference | None:
    """A ``t.me`` link as the channel it points at, or ``None``.

    Handled: ``t.me/name`` (a channel) and ``t.me/name/123`` (one message
    within it — the channel, carrying the post id), ``t.me/s/name`` (the
    web preview), and ``t.me/c/<id>`` / ``t.me/c/<id>/<msg>`` (a bare
    channel id, with the post id when present). Refused: ``t.me/joinchat/
    ...`` and ``t.me/+...``, which are invites resolvable only by someone
    already let in, and any non-``t.me`` host.
    """
    raw = url.strip()
    # A bare `t.me/foo` has no `//`, so urlsplit would read `t.me` as the
    # path and find no host; prefixing `//` makes it a network-path URL.
    if "//" not in raw:
        raw = "//" + raw
    host = urlsplit(raw).netloc.lower().removeprefix("www.")
    if host not in _TME_HOSTS:
        return None

    segments = [
        segment for segment in urlsplit(raw).path.split("/") if segment
    ]
    if not segments:
        return None

    first = segments[0]
    if first.startswith("+") or first.lower() == "joinchat":
        return None
    if first.lower() == "s":
        # t.me/s/<name> — the same channel, in its web-preview form.
        return _username_reference(segments[1]) if len(segments) > 1 else None
    if first.lower() == "c":
        # t.me/c/<id>/<msg> — the bare id, no `-100` prefix, exactly the
        # form `channels.tg_id` stores. The message id, when present, is
        # the third segment.
        if len(segments) > 1 and segments[1].isdigit():
            msg_id = _post_id(segments[2]) if len(segments) > 2 else None
            return Reference(channel_id=int(segments[1]), msg_id=msg_id)
        return None
    # t.me/<name> or t.me/<name>/<msg> — the second segment, when it is a
    # message id, points at one post of the channel.
    msg_id = _post_id(segments[1]) if len(segments) > 1 else None
    return _username_reference(first, msg_id=msg_id)


def extract_references(payload: dict[str, Any]) -> list[Reference]:
    """Every channel a message references by entity, in order seen.

    Reads the three entity shapes that can name a channel: an
    ``@mention``, a plain URL (``t.me/...`` written out in the text), and
    a hyperlink whose visible text hides a URL. Deduplication is the
    caller's job — the same channel referenced twice is two references
    here and one edge there.
    """
    text = payload.get("message") or ""
    references: list[Reference] = []
    for entity in payload.get("entities") or []:
        kind = entity.get("_")
        if kind == "MessageEntityMention":
            username = normalize_username(
                _slice_utf16(
                    text, entity.get("offset", 0), entity.get("length", 0)
                )
            )
            if username is not None:
                references.append(Reference(username=username))
        elif kind == "MessageEntityUrl":
            reference = parse_tme_link(
                _slice_utf16(
                    text, entity.get("offset", 0), entity.get("length", 0)
                )
            )
            if reference is not None:
                references.append(reference)
        elif kind == "MessageEntityTextUrl":
            reference = parse_tme_link(entity.get("url") or "")
            if reference is not None:
                references.append(reference)
    return references


def _username_reference(
    segment: str, msg_id: int | None = None
) -> Reference | None:
    username = normalize_username(segment)
    if username is None:
        return None
    return Reference(username=username, msg_id=msg_id)


def _post_id(segment: str) -> int | None:
    """A path segment as a message id, or ``None`` if it is not one.

    A message id is a run of digits — ``t.me/name/123``. Anything else in
    that position (a comment thread's ``?comment=`` is already stripped as
    a query, a stray word) names no post and is dropped rather than
    guessed at.
    """
    return int(segment) if segment.isdigit() else None


def _as_datetime(raw: Any) -> datetime | None:
    """A stored ISO-8601 date string as a datetime, or ``None``.

    The raw layer serializes every date to an ISO-8601 string (see
    ``tg.payload``); a header without a date, or a malformed one, is
    nothing rather than an error.
    """
    return datetime.fromisoformat(raw) if isinstance(raw, str) else None


def _slice_utf16(text: str, offset: int, length: int) -> str:
    """The substring an entity covers.

    Telegram entity offsets and lengths are counted in **UTF-16 code
    units**, not Python characters. One astral character — an emoji — is
    two units but one ``str`` index, so slicing ``text`` directly puts
    every offset past the first emoji one unit too early and hands back a
    username missing its first letter. Re-encoding to UTF-16 makes the
    units line up with the offsets.
    """
    units = text.encode("utf-16-le")
    piece = units[offset * 2 : (offset + length) * 2]
    return piece.decode("utf-16-le", errors="ignore")
