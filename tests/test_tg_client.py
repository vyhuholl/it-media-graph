from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from typing import Any
from unittest.mock import AsyncMock, MagicMock

import pytest

from itgraph.db import session_lease as session_lease_module
from itgraph.tg import client as tg_client


class FakeClient:
    """Stands in for TelegramClient. Nothing here touches the network."""

    def __init__(self, *, authorized: bool) -> None:
        self.connect = AsyncMock()
        self.disconnect = AsyncMock()
        self.is_user_authorized = AsyncMock(return_value=authorized)


@pytest.fixture
def fake(monkeypatch: pytest.MonkeyPatch) -> MagicMock:
    factory = MagicMock()
    monkeypatch.setattr(tg_client, "build_client", factory)
    return factory


def test_build_client_passes_device_metadata(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    captured: dict[str, Any] = {}

    def spy(*args: Any, **kwargs: Any) -> object:
        captured.update(kwargs)
        captured["args"] = args
        return object()

    monkeypatch.setattr(tg_client, "TelegramClient", spy)
    tg_client.build_client()

    assert captured["device_model"] == tg_client.settings.device_model
    assert captured["args"][1] == tg_client.settings.telegram_api_id


async def test_connected_yields_and_disconnects(fake: MagicMock) -> None:
    client = FakeClient(authorized=True)
    fake.return_value = client

    async with tg_client.connected("backfill") as connection:
        assert connection is client
        client.connect.assert_awaited_once()
        client.disconnect.assert_not_awaited()

    client.disconnect.assert_awaited_once()


async def test_connected_refuses_an_unauthorized_session(
    fake: MagicMock,
) -> None:
    client = FakeClient(authorized=False)
    fake.return_value = client

    with pytest.raises(tg_client.NotAuthorizedError):
        async with tg_client.connected("backfill"):
            pass

    # Still hung up, even on the error path.
    client.disconnect.assert_awaited_once()


async def test_connected_claims_the_session_lease(
    fake: MagicMock, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The lease is taken here so that no command can forget to take it.

    Asserted at this seam rather than per command precisely because that
    is the argument for putting it here: every networked command goes
    through `connected`, so one test covers all of them, and a command
    added later is covered before it is written.
    """
    taken: list[str] = []

    @asynccontextmanager
    async def recording(command: str, **kwargs: Any) -> AsyncIterator[None]:
        taken.append(command)
        yield

    monkeypatch.setattr(session_lease_module, "session_lease", recording)
    fake.return_value = FakeClient(authorized=True)

    async with tg_client.connected("metadata"):
        pass

    assert taken == ["metadata"]


async def test_the_lease_outlives_the_disconnect(
    fake: MagicMock, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Released after the client hangs up, never before.

    Telethon writes the session file on the way out — that is what
    `persist_peers` exists to make explicit — so a lease released before
    the disconnect would leave the last write unprotected.
    """
    order: list[str] = []
    client = FakeClient(authorized=True)
    client.disconnect = AsyncMock(side_effect=lambda: order.append("hung up"))
    fake.return_value = client

    @asynccontextmanager
    async def recording(command: str, **kwargs: Any) -> AsyncIterator[None]:
        try:
            yield
        finally:
            order.append("lease released")

    monkeypatch.setattr(session_lease_module, "session_lease", recording)

    async with tg_client.connected("backfill"):
        pass

    assert order == ["hung up", "lease released"]
