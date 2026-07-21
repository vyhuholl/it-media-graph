"""Interactive authorization flows. Only ``itgraph login`` uses these.

Kept out of ``client.py`` on purpose: everything else in the package
connects an already-authorized session and must never be able to reach a
prompt.
"""

import io
import logging
from collections.abc import Callable
from typing import Any

import qrcode
from telethon import TelegramClient
from telethon.errors import SessionPasswordNeededError

__all__ = ["QR_REFRESH_SECONDS", "authorize_qr", "render_qr"]

logger = logging.getLogger(__name__)

# Telegram expires a QR token in well under a minute, and an expired one
# scans as an error rather than as nothing. Re-issue with room to spare.
QR_REFRESH_SECONDS = 25


def render_qr(url: str) -> str:
    """Draw a login URL as an ASCII QR code."""
    code = qrcode.QRCode()
    code.add_data(url)
    buffer = io.StringIO()
    # invert=True renders dark modules as light ones, which is what a
    # scanner expects from a terminal with a dark background.
    code.print_ascii(out=buffer, invert=True)
    return buffer.getvalue()


async def authorize_qr(
    client: TelegramClient,
    *,
    show: Callable[[str], None],
    ask_password: Callable[[], str],
) -> Any:
    """Authorize by QR code, refreshing the token until it is scanned.

    Sidesteps code delivery entirely: Telegram confirms the login on a
    device that is already signed in. Useful when the login code never
    arrives, which the caller cannot fix from this side.

    ``show`` renders one frame; it is called again for every refreshed
    token. ``ask_password`` is only called if two-step verification is
    on, and must not echo what it reads.
    """
    qr = await client.qr_login()
    while True:
        show(render_qr(qr.url))
        try:
            return await qr.wait(timeout=QR_REFRESH_SECONDS)
        except TimeoutError:
            logger.debug("qr token expired unscanned, re-issuing")
            await qr.recreate()
        except SessionPasswordNeededError:
            # The scan confirmed the device; the cloud password is a
            # second, separate step.
            return await client.sign_in(password=ask_password())
