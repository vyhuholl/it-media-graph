"""Stand-ins for Telethon objects. Nothing here touches a socket."""

from collections.abc import AsyncIterator
from typing import Any


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


class FakeTelegramClient:
    """A client that yields a fixed dialog list."""

    def __init__(self, records: list[dict[str, Any]]) -> None:
        self.records = records

    def iter_dialogs(self) -> AsyncIterator[FakeDialog]:
        async def dialogs() -> AsyncIterator[FakeDialog]:
            for record in self.records:
                yield FakeDialog(record)

        return dialogs()
