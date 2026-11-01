"""Asking YouTube to search: ``search.list``, one page at a time.

A unit a call, charged to the Search Queries bucket — 100 calls a day,
outside the 10,000 — which is why the command that makes these requests
has a pool of its own (``db/yt_quota.py``).

What is asked for, and why:

- 50 results a page: the parameter defaults to 5, and the price is per
  call;
- ``relevanceLanguage=en``: it ranks by language rather than filters by
  it, and the inventory being built is English-language. No
  ``regionCode`` — it filters by where a video can be watched, not where
  a channel is from;
- for videos, ``publishedAfter``, so every result is a recent upload and
  every channel it names publishes now;
- the default order, YouTube's relevance, which is the order the queue
  keeps.

What is read: the id of the channel each result belongs to, and the
token for the next page — ``snippet/channelId`` for a video, the
publisher; ``id/channelId`` for a channel. Not the title, description,
thumbnails, channel title, publication time or video id, which the only
part, ``snippet``, would otherwise carry.
"""

import logging
from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Any

from itgraph.db.models import YtSearchType
from itgraph.yt.client import YouTube
from itgraph.yt_entries import CHANNEL_ID

__all__ = ["FIELDS", "LANGUAGE", "METHOD", "RESULTS_PER_PAGE", "Page", "page"]

logger = logging.getLogger(__name__)

METHOD = "search.list"
RESULTS_PER_PAGE = 50
LANGUAGE = "en"
FIELDS = {
    YtSearchType.VIDEO: "nextPageToken,items(snippet/channelId)",
    YtSearchType.CHANNEL: "nextPageToken,items(id/channelId)",
}


@dataclass(frozen=True, slots=True)
class Page:
    """One page of results: each result's channel, in YouTube's order.

    ``channels`` holds one id per result, so a channel that uploaded two
    of the videos found appears twice; ``None`` is a result whose channel
    could not be read, kept so that positions stay YouTube's.
    """

    channels: list[str | None] = field(default_factory=list)
    next_token: str | None = None


def _rfc3339(moment: datetime) -> str:
    return moment.astimezone(UTC).strftime("%Y-%m-%dT%H:%M:%SZ")


def _channel_of(item: Any, kind: YtSearchType) -> str | None:
    if not isinstance(item, dict):
        return None
    holder = item.get("snippet" if kind is YtSearchType.VIDEO else "id")
    channel = holder.get("channelId") if isinstance(holder, dict) else None
    if isinstance(channel, str) and CHANNEL_ID.match(channel):
        return channel
    return None


async def page(
    youtube: YouTube,
    query: str,
    kind: YtSearchType,
    *,
    published_after: datetime | None = None,
    token: str | None = None,
) -> Page:
    """One page of a search, read down to each result's channel id."""
    params = {
        "part": "snippet",
        "q": query,
        "type": kind.value,
        "maxResults": str(RESULTS_PER_PAGE),
        "relevanceLanguage": LANGUAGE,
        "fields": FIELDS[kind],
    }
    if published_after is not None:
        params["publishedAfter"] = _rfc3339(published_after)
    if token is not None:
        params["pageToken"] = token
    body = await youtube.get(METHOD, params)
    if body is None:
        return Page()
    items = body.get("items")
    channels = [
        _channel_of(item, kind)
        for item in (items if isinstance(items, list) else ())
    ]
    unread = channels.count(None)
    if unread:
        logger.warning("%d search results named no readable channel", unread)
    next_token = body.get("nextPageToken")
    return Page(
        channels=channels,
        next_token=next_token if isinstance(next_token, str) else None,
    )
