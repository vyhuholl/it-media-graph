import logging
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from typing import Any
from unittest.mock import AsyncMock, MagicMock

import pytest
from pydantic import SecretStr

from itgraph.config import ProxyType
from itgraph.db import session_lease as session_lease_module
from itgraph.tg import client as tg_client

# Obviously fake, because `tests/` is not excluded from the gitleaks
# hook and a realistic-looking value would trip it.
PROXY_PASSWORD = "test-proxy-password"


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


@pytest.fixture
def captured(monkeypatch: pytest.MonkeyPatch) -> dict[str, Any]:
    """What `build_client` would hand to Telethon. Constructs nothing."""
    seen: dict[str, Any] = {}

    def spy(*args: Any, **kwargs: Any) -> object:
        seen.update(kwargs)
        seen["args"] = args
        return object()

    monkeypatch.setattr(tg_client, "TelegramClient", spy)
    return seen


def configure_proxy(monkeypatch: pytest.MonkeyPatch, **overrides: Any) -> None:
    values: dict[str, Any] = {
        "proxy_type": ProxyType.SOCKS5,
        "proxy_host": "proxy.invalid",
        "proxy_port": 1080,
        "proxy_username": None,
        "proxy_password": None,
    }
    values.update(overrides)
    for name, value in values.items():
        monkeypatch.setattr(tg_client.settings, name, value)


def test_a_configured_proxy_reaches_telethon(
    captured: dict[str, Any], monkeypatch: pytest.MonkeyPatch
) -> None:
    configure_proxy(monkeypatch)
    tg_client.build_client()

    assert captured["proxy"] == {
        "proxy_type": "socks5",
        "addr": "proxy.invalid",
        "port": 1080,
    }


def test_an_unset_proxy_passes_nothing(captured: dict[str, Any]) -> None:
    """Not `None`, not an empty tuple — the argument is absent.

    A direct connection has to be the call it was before proxies were
    supported, so that configuring nothing changes nothing.
    """
    tg_client.build_client()

    assert "proxy" not in captured


def test_proxy_credentials_are_passed_when_set(
    captured: dict[str, Any], monkeypatch: pytest.MonkeyPatch
) -> None:
    configure_proxy(
        monkeypatch,
        proxy_username="collector",
        proxy_password=SecretStr(PROXY_PASSWORD),
    )
    tg_client.build_client()

    assert captured["proxy"]["username"] == "collector"
    assert captured["proxy"]["password"] == PROXY_PASSWORD


def test_absent_credentials_are_omitted_not_none(
    captured: dict[str, Any], monkeypatch: pytest.MonkeyPatch
) -> None:
    """A proxy that takes no authentication is ordinary."""
    configure_proxy(monkeypatch)
    tg_client.build_client()

    assert "username" not in captured["proxy"]
    assert "password" not in captured["proxy"]


def test_the_route_is_reported(
    captured: dict[str, Any],
    monkeypatch: pytest.MonkeyPatch,
    caplog: pytest.LogCaptureFixture,
) -> None:
    """The one way to notice a deployment that came up unproxied.

    A `.env` that did not get copied produces a collector that works,
    logs nothing unusual, and reaches Telegram from the address the
    proxy exists to hide.
    """
    configure_proxy(
        monkeypatch,
        proxy_username="collector",
        proxy_password=SecretStr(PROXY_PASSWORD),
    )
    with caplog.at_level(logging.INFO, logger="itgraph.tg.client"):
        tg_client.build_client()

    assert "proxy.invalid" in caplog.text
    assert "1080" in caplog.text
    assert PROXY_PASSWORD not in caplog.text


def test_a_direct_route_is_reported_too(
    captured: dict[str, Any], caplog: pytest.LogCaptureFixture
) -> None:
    with caplog.at_level(logging.INFO, logger="itgraph.tg.client"):
        tg_client.build_client()

    assert "direct" in caplog.text


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
