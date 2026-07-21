from typing import Any
from unittest.mock import AsyncMock, MagicMock

import pytest

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

    async with tg_client.connected() as connection:
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
        async with tg_client.connected():
            pass

    # Still hung up, even on the error path.
    client.disconnect.assert_awaited_once()
