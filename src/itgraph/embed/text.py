"""What is embedded: the texts the raw layer already holds.

Telegram's text is the payload's ``message``, and the raw layer keeps
the first fetch of it, so a post edited afterwards stays as it was
embedded. YouTube's text is the title and the description joined into
one — a video with no description embeds the title alone — and the
joined text's hash rides beside the embedding, because YouTube's raw
layer is refreshed and a changed text is re-embedded.

``db.embed`` states the same YouTube join as a SQL expression, to select
due rows without shipping every payload to Python; ``tests/
test_embed_text.py`` runs both over the same fixtures, so the two
definitions cannot drift apart quietly.
"""

import hashlib
from typing import Any


def tg_text(payload: dict[str, Any]) -> str | None:
    """A message's text, or ``None`` for a media-only message.

    Empty is no text: Telegram writes ``message`` as ``""`` when a post
    is a photo or a video with a caption-less upload, and embedding an
    empty string would put a meaningless vector in the table.
    """
    text = payload.get("message")
    if not isinstance(text, str) or not text:
        return None
    return text


def yt_text(payload: dict[str, Any]) -> str | None:
    """A video's title and description, joined by a blank line.

    The title alone when there is no description (6.3% of the corpus),
    and ``None`` for a payload without a title — a shape YouTube does
    not return, and not something to embed a vector from.
    """
    snippet = payload.get("snippet") or {}
    title = snippet.get("title")
    description = snippet.get("description")
    if not isinstance(title, str) or not title:
        return None
    if not isinstance(description, str) or not description:
        return title
    return f"{title}\n\n{description}"


def content_hash(text: str) -> str:
    """The hash a YouTube embedding names its text by.

    sha256 over the UTF-8 bytes, hex, so the selection query and Python
    agree character for character; the same expression runs in SQL as
    ``encode(sha256(convert_to(<text>, 'UTF8')), 'hex')``.
    """
    return hashlib.sha256(text.encode("utf-8")).hexdigest()
