from typing import Any
from unittest.mock import AsyncMock, MagicMock

import pytest
from telethon.errors import SessionPasswordNeededError

from itgraph.tg import auth


class FakeQRLogin:
    """A ``telethon.tl.custom.QRLogin`` that never touches a socket.

    ``outcomes`` is consumed one entry per ``wait`` call: an exception
    instance is raised, anything else is returned.
    """

    def __init__(self, outcomes: list[Any]) -> None:
        self.outcomes = list(outcomes)
        self.url = "tg://login?token=first"
        self.recreated = 0

    async def wait(self, timeout: float | None = None) -> Any:
        outcome = self.outcomes.pop(0)
        if isinstance(outcome, Exception):
            raise outcome
        return outcome

    async def recreate(self) -> None:
        self.recreated += 1
        self.url = f"tg://login?token=retry{self.recreated}"


def build_client(outcomes: list[Any]) -> tuple[MagicMock, FakeQRLogin]:
    qr = FakeQRLogin(outcomes)
    client = MagicMock()
    client.qr_login = AsyncMock(return_value=qr)
    client.sign_in = AsyncMock(return_value="signed-in")
    return client, qr


def refuse_password() -> str:
    raise AssertionError("must not ask for a password")


def test_render_qr_is_ascii_art() -> None:
    art = auth.render_qr("tg://login?token=abc")

    assert art.strip()
    # Blocks and spacing only: anything else would not scan. qrcode draws
    # the light modules with a non-breaking space, so terminals cannot
    # collapse or wrap them.
    assert set(art) <= {"\n", "\xa0", "█", "▀", "▄"}


async def test_authorize_qr_returns_the_scanned_user() -> None:
    client, _ = build_client(["me"])
    frames: list[str] = []

    user = await auth.authorize_qr(
        client, show=frames.append, ask_password=refuse_password
    )

    assert user == "me"
    assert len(frames) == 1


async def test_authorize_qr_reissues_an_expired_token() -> None:
    client, qr = build_client([TimeoutError(), TimeoutError(), "me"])
    frames: list[str] = []

    user = await auth.authorize_qr(
        client, show=frames.append, ask_password=refuse_password
    )

    assert user == "me"
    assert qr.recreated == 2
    # One frame per token, so the user always scans a live one.
    assert len(frames) == 3
    assert frames[0] != frames[-1]


async def test_authorize_qr_asks_for_the_2fa_password() -> None:
    client, _ = build_client([SessionPasswordNeededError(request=None)])

    user = await auth.authorize_qr(
        client, show=lambda _: None, ask_password=lambda: "hunter-two"
    )

    assert user == "signed-in"
    client.sign_in.assert_awaited_once_with(password="hunter-two")


async def test_authorize_qr_propagates_an_unexpected_failure() -> None:
    client, _ = build_client([RuntimeError("dc down")])

    with pytest.raises(RuntimeError, match="dc down"):
        await auth.authorize_qr(
            client, show=lambda _: None, ask_password=refuse_password
        )
