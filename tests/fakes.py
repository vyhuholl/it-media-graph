"""Stand-ins for Telethon objects. Nothing here touches a socket."""

from collections.abc import AsyncIterator
from datetime import UTC, datetime, timedelta
from typing import Any

from telethon.errors import FloodWaitError
from telethon.tl.types import Channel as TLChannel
from telethon.tl.types import User as TLUser


def tl_channel(
    tg_id: int,
    *,
    username: str | None = None,
    title: str = "",
    megagroup: bool = False,
) -> TLChannel:
    """A real Telethon ``Channel``, so ``isinstance`` checks are honest.

    Resolution accepts only a genuine ``Channel``; the fake must return
    the real type, not a look-alike, or the acceptance test proves
    nothing.
    """
    return TLChannel(
        id=tg_id,
        title=title,
        photo=None,
        date=datetime(2026, 1, 1, tzinfo=UTC),
        username=username,
        megagroup=megagroup,
    )


def tl_user(tg_id: int, *, bot: bool = False) -> TLUser:
    """A real Telethon ``User`` — what a mention of a person resolves to."""
    return TLUser(id=tg_id, bot=bot)


class FakeEntity:
    """A Telethon channel, chat or user entity."""

    def __init__(self, record: dict[str, Any]) -> None:
        self.id = record["id"]
        self.title = record["title"]
        # Telethon's basic-group entity has no username attribute at
        # all, so an absent username is absent, not None.
        if record["username"] is not None:
            self.username = record["username"]


class FakeDialog:
    """A ``telethon.tl.custom.Dialog``.

    A supergroup is both a channel and a group, exactly as Telethon
    reports it.
    """

    def __init__(self, record: dict[str, Any]) -> None:
        self.entity = FakeEntity(record)
        self.is_user = record["type"] == "user"
        self.is_group = record["type"] in {"group", "megagroup"}
        self.is_channel = record["type"] in {"channel", "megagroup"}


def message_to_dict() -> dict[str, Any]:
    """A message shaped the way Telethon's ``.to_dict()`` shapes one.

    Synthetic throughout — invented ids, invented text. What it reproduces
    faithfully is the *shape*: the ``_`` type tags, the nesting, and the
    two types the standard JSON encoder rejects. ``date`` is a datetime
    and ``file_reference`` is raw bytes, and both appear at more than one
    depth, including inside a list.
    """
    return {
        "_": "Message",
        "id": 4242,
        "peer_id": {"_": "PeerChannel", "channel_id": 1000000001},
        "date": datetime(2026, 3, 14, 9, 26, 53, tzinfo=UTC),
        "message": "смотрите, что нашли в опенсорсе",
        "out": False,
        "silent": False,
        "views": 1337,
        "edit_date": None,
        # A forward is what the whole graph is built from, and it carries
        # a date of its own.
        "fwd_from": {
            "_": "MessageFwdHeader",
            "date": datetime(2026, 3, 13, 18, 2, 11, tzinfo=UTC),
            "from_id": {"_": "PeerChannel", "channel_id": 1000000002},
            "post_author": None,
        },
        "media": {
            "_": "MessageMediaPhoto",
            "photo": {
                "_": "Photo",
                "id": 5555555555555555555,
                "access_hash": -1234567890123456789,
                "file_reference": b"\x02\x9a\xff\x00binary-ish",
                "date": datetime(2026, 3, 14, 9, 26, 50, tzinfo=UTC),
                "sizes": [
                    {
                        "_": "PhotoStrippedSize",
                        "type": "i",
                        "bytes": b"\x01\x1a",
                    },
                    {"_": "PhotoSize", "type": "m", "w": 320, "h": 240},
                ],
            },
        },
        "entities": [
            {"_": "MessageEntityUrl", "offset": 0, "length": 12},
            {"_": "MessageEntityMention", "offset": 20, "length": 8},
        ],
        "restriction_reason": [],
    }


class FakeMessage:
    """Anything with a ``.to_dict()``, which is all the encoder wants."""

    def __init__(self, payload: dict[str, Any] | None = None) -> None:
        self._payload = payload if payload is not None else message_to_dict()

    def to_dict(self) -> dict[str, Any]:
        return self._payload


class FakeChannel:
    """A resolved channel or chat entity."""

    def __init__(
        self,
        tg_id: int,
        username: str | None = None,
        title: str | None = None,
    ) -> None:
        self.id = tg_id
        self.title = title
        if username is not None:
            self.username = username


class FakeSession:
    """The session file's entity cache, and nothing else.

    Exists because production asks the *session* for a peer, not the
    client. The difference is the whole point of that code: the client
    would go to ``contacts.resolveUsername`` on a miss, and the session
    just says no. A fake that answered on the client would let a
    regression through silently, which is the one thing this cache is
    guarding against.

    Synchronous, like Telethon's bundled SQLite session — the caller
    wraps it in ``maybe_async``, so both shapes work and this is the one
    the real code meets.

    **Keyed by channel id**, like the real entity table, whose username
    column is nullable and empty for any channel that publishes its
    handle through the newer multiple-usernames list. A fake keyed by
    name would answer lookups the real session cannot, which is exactly
    the regression that stranded six channels.
    """

    def __init__(self, cached_peers: dict[int, Any]) -> None:
        self.cached_peers = cached_peers
        self.lookups: list[Any] = []
        self.saves = 0

    def get_input_entity(self, ref: Any) -> Any:
        self.lookups.append(ref)
        if isinstance(ref, str):
            # The real session can answer by name; production deliberately
            # no longer asks that way, so answering here would hide a
            # regression rather than model one.
            raise TypeError(
                "the session is keyed by channel id, not by username"
            )
        key = ref if isinstance(ref, int) else ref.channel_id
        try:
            return self.cached_peers[key]
        except KeyError:
            raise ValueError(f"no cached peer for {key}") from None

    def save(self) -> None:
        """Commit the learned entities. Counted, so a test can prove it ran.

        Synchronous, like Telethon's bundled SQLite session — production
        wraps it in ``maybe_async``, so both shapes work and this is the
        one the real code meets.
        """
        self.saves += 1


class FakeInputPeer:
    """What ``get_input_entity`` answers with: an ``InputPeerChannel``.

    Spells the id ``channel_id``, not ``id``, because that is what
    Telethon's real input peer does — and the difference is not cosmetic
    here. An input peer is the argument that keeps a request from
    reaching ``contacts.resolveUsername``, so a fake that let ``.id``
    work would hide the one shape change the production code makes.
    """

    def __init__(self, channel: FakeChannel) -> None:
        self.channel_id = channel.id
        # Present because a real one carries it; nothing here reads it.
        self.access_hash = -channel.id


class FakeFullChannel:
    """A ``messages.ChatFull``, as ``GetFullChannelRequest`` returns one.

    The response already carries the linked chat in ``chats``, which is
    why resolving the link costs no second request.
    """

    def __init__(
        self,
        channel: FakeChannel,
        *,
        linked_chat: FakeChannel | None = None,
        about: str = "",
    ) -> None:
        self.full_chat = FakeChannelFull(
            channel.id,
            linked_chat_id=None if linked_chat is None else linked_chat.id,
            about=about,
        )
        self.chats = [channel] + ([linked_chat] if linked_chat else [])
        self.users: list[Any] = []

    def to_dict(self) -> dict[str, Any]:
        return {
            "_": "messages.ChatFull",
            "full_chat": {
                "_": "ChannelFull",
                "id": self.full_chat.id,
                "about": self.full_chat.about,
                "linked_chat_id": self.full_chat.linked_chat_id,
                "participants_count": 4321,
                # Both types the plain JSON encoder rejects, so the
                # metadata path exercises the encoder for real.
                "chat_photo": {
                    "_": "Photo",
                    "date": datetime(2026, 1, 9, 7, 0, tzinfo=UTC),
                    "file_reference": b"\x00\xc3reference",
                },
            },
            "chats": [
                {
                    "_": "Channel",
                    "id": chat.id,
                    "title": chat.title,
                    "username": getattr(chat, "username", None),
                }
                for chat in self.chats
            ],
            "users": [],
        }


class FakeChannelFull:
    """The ``full_chat`` half of the response."""

    def __init__(
        self, tg_id: int, *, linked_chat_id: int | None, about: str
    ) -> None:
        self.id = tg_id
        self.linked_chat_id = linked_chat_id
        self.about = about


class FakeHistoryMessage:
    """A message as ``iter_messages`` yields it."""

    def __init__(self, msg_id: int, date: datetime, text: str = "") -> None:
        self.id = msg_id
        self.date = date
        self.message = text

    def to_dict(self) -> dict[str, Any]:
        return {
            "_": "Message",
            "id": self.id,
            "date": self.date,
            "message": self.message,
            "peer_id": {"_": "PeerChannel", "channel_id": 1000000001},
        }


def history(
    count: int,
    *,
    newest_id: int = 1000,
    newest: datetime | None = None,
    step_days: int = 1,
) -> list[FakeHistoryMessage]:
    """``count`` messages, newest first, one every ``step_days``."""
    start = newest or datetime(2026, 6, 1, 12, 0, tzinfo=UTC)
    return [
        FakeHistoryMessage(
            newest_id - index,
            start - timedelta(days=index * step_days),
            f"post {newest_id - index}",
        )
        for index in range(count)
    ]


class FakeTelegramClient:
    """A client that yields a fixed dialog list.

    Optionally also resolves usernames, answers requests and serves
    history, for the collection paths. ``resolved`` records every
    username asked for and ``windows`` every history request, so a test
    can assert what the walker actually spent.
    """

    def __init__(
        self,
        records: list[dict[str, Any]] | None = None,
        *,
        entities: dict[str, FakeChannel] | None = None,
        entities_by_id: dict[int, Any] | None = None,
        full_channels: dict[int, FakeFullChannel] | None = None,
        histories: dict[int, list[FakeHistoryMessage]] | None = None,
        flood_on_window: dict[int, int] | None = None,
        flood_on_request: dict[int, int] | None = None,
        resolve_floods: dict[str | int, int] | None = None,
        raises: BaseException | None = None,
        raises_for: dict[int, BaseException] | None = None,
        cached_peers: dict[int, Any] | None = None,
        flood_request: Any = None,
    ) -> None:
        # What a raised FloodWaitError names as its cause. `None` is the
        # shape a rate limit that named no request has; a test that cares
        # about the recorded method passes a real one.
        self.flood_request = flood_request
        self.records = records or []
        self.entities = entities or {}
        # What the session file could answer without a network call.
        # Defaults to everything this client knows, which is the normal
        # case; pass `{}` for a session that has never seen the channel.
        self.session = FakeSession(
            {
                channel.id: FakeInputPeer(channel)
                for channel in (entities or {}).values()
            }
            if cached_peers is None
            else cached_peers
        )
        # Resolution by id reaches here: a channel discovered by forward
        # is looked up through a `PeerChannel`, keyed by its channel id.
        self.entities_by_id = entities_by_id or {}
        self.full_channels = full_channels or {}
        self.histories = histories or {}
        # window index -> seconds to claim the flood will last
        self.flood_on_window = flood_on_window or {}
        # channel id -> seconds, for the metadata request. Fires once,
        # so a wait short enough to sleep off is followed by a success
        # and a wait long enough to halt still only happens once.
        self.flood_on_request = flood_on_request or {}
        self._flooded_requests: set[int] = set()
        # lookup key (username or id) -> seconds to flood, once, before
        # the retry succeeds.
        self.resolve_floods = resolve_floods or {}
        self._flooded: set[str | int] = set()
        self.raises = raises
        # Per-channel, for the history walk. A walk no longer looks a
        # channel up before reading it, so "this channel is unreachable"
        # can only be said at the point history is asked for — which is
        # also where Telegram would say it.
        self.raises_for = raises_for or {}
        self.resolved: list[str | int] = []
        self.requests: list[Any] = []
        self.windows: list[tuple[int, int, int]] = []
        self.downloads: list[Any] = []

    async def download_media(self, *args: Any, **kwargs: Any) -> None:
        """Never called. Recorded so a test can prove it never was."""
        self.downloads.append(args)

    def iter_messages(
        self, entity: Any, *, limit: int = 100, offset_id: int = 0
    ) -> AsyncIterator[FakeHistoryMessage]:
        # An input peer, so `channel_id` — the walk is handed exactly what
        # the session cache answered with, and nothing converts it.
        channel_id = entity.channel_id
        index = len(self.windows)
        self.windows.append((channel_id, offset_id, limit))
        flood_seconds = self.flood_on_window.get(index)

        async def walk() -> AsyncIterator[FakeHistoryMessage]:
            if flood_seconds is not None:
                raise FloodWaitError(
                    request=self.flood_request, capture=flood_seconds
                )
            # Where a channel now turns out to be unreachable. The walk
            # used to find out from the metadata request that opened it;
            # with that gone, the first history request is what discovers
            # a private or deleted channel — one request either way.
            error = self.raises_for.get(channel_id, self.raises)
            if error is not None:
                raise error
            messages = self.histories.get(channel_id, [])
            # offset_id 0 means "from the newest"; otherwise strictly
            # older than it, the way Telegram walks backwards.
            older = [m for m in messages if offset_id == 0 or m.id < offset_id]
            for message in older[:limit]:
                yield message

        return walk()

    def iter_dialogs(self) -> AsyncIterator[FakeDialog]:
        async def dialogs() -> AsyncIterator[FakeDialog]:
            for record in self.records:
                yield FakeDialog(record)

        return dialogs()

    async def get_entity(self, ref: Any) -> Any:
        # A username is a str; a channel id arrives as a `PeerChannel`
        # (or a bare int). The key is what a test asserts was asked for.
        if isinstance(ref, str | int):
            key: str | int = ref
        else:
            key = ref.channel_id
        self.resolved.append(key)

        flood = self.resolve_floods.get(key)
        if flood is not None and key not in self._flooded:
            self._flooded.add(key)
            raise FloodWaitError(request=self.flood_request, capture=flood)

        if self.raises is not None:
            raise self.raises

        if isinstance(ref, str):
            try:
                return self.entities[ref]
            except KeyError:
                raise ValueError(f"no entity @{ref}") from None
        try:
            return self.entities_by_id[key]
        except KeyError:
            raise ValueError(f"no entity for id {key}") from None

    # Deliberately no `get_input_entity` on the client. Telethon's goes to
    # the network on a cache miss, which is the request a history walk
    # must never make — so production asks `client.session` instead, and
    # a fake that offered the client method would quietly accept the
    # mistake it exists to catch.

    @property
    def cached_peers(self) -> dict[int, Any]:
        """What the session file holds, by channel id. `= {}` is cold."""
        return self.session.cached_peers

    @cached_peers.setter
    def cached_peers(self, peers: dict[int, Any]) -> None:
        self.session.cached_peers = peers

    @property
    def input_entities(self) -> list[Any]:
        """Peer lookups, recorded apart from ``resolved``.

        The whole point of the cached path is that it is *not* a
        `contacts.resolveUsername`; a test that cannot tell the two apart
        cannot show the walk stayed off the quota.
        """
        return self.session.lookups

    async def __call__(self, request: Any) -> Any:
        self.requests.append(request)
        # `request.channel` is the input peer the caller passed straight
        # through. Keying on `.id` would accept a resolved entity too,
        # and accepting one is what production must no longer do.
        channel_id = request.channel.channel_id

        seconds = self.flood_on_request.get(channel_id)
        if seconds is not None and channel_id not in self._flooded_requests:
            self._flooded_requests.add(channel_id)
            raise FloodWaitError(request=self.flood_request, capture=seconds)

        return self.full_channels[channel_id]
