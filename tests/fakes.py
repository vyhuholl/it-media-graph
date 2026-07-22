"""Stand-ins for Telethon objects. Nothing here touches a socket."""

from collections.abc import AsyncIterator
from datetime import UTC, datetime, timedelta
from typing import Any

from telethon.errors import FloodWaitError


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
        full_channels: dict[int, FakeFullChannel] | None = None,
        histories: dict[int, list[FakeHistoryMessage]] | None = None,
        flood_on_window: dict[int, int] | None = None,
        raises: BaseException | None = None,
    ) -> None:
        self.records = records or []
        self.entities = entities or {}
        self.full_channels = full_channels or {}
        self.histories = histories or {}
        # window index -> seconds to claim the flood will last
        self.flood_on_window = flood_on_window or {}
        self.raises = raises
        self.resolved: list[str] = []
        self.requests: list[Any] = []
        self.windows: list[tuple[int, int, int]] = []
        self.downloads: list[Any] = []

    async def download_media(self, *args: Any, **kwargs: Any) -> None:
        """Never called. Recorded so a test can prove it never was."""
        self.downloads.append(args)

    def iter_messages(
        self, entity: Any, *, limit: int = 100, offset_id: int = 0
    ) -> AsyncIterator[FakeHistoryMessage]:
        index = len(self.windows)
        self.windows.append((entity.id, offset_id, limit))
        flood_seconds = self.flood_on_window.get(index)

        async def walk() -> AsyncIterator[FakeHistoryMessage]:
            if flood_seconds is not None:
                raise FloodWaitError(request=None, capture=flood_seconds)
            messages = self.histories.get(entity.id, [])
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

    async def get_entity(self, username: str) -> FakeChannel:
        self.resolved.append(username)
        if self.raises is not None:
            raise self.raises
        try:
            return self.entities[username]
        except KeyError:
            raise ValueError(f"no entity @{username}") from None

    async def __call__(self, request: Any) -> Any:
        self.requests.append(request)
        return self.full_channels[request.channel.id]
