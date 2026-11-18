"""Reading a region's chart: ``videos.list`` ``chart=mostPopular``.

One unit a page, fifty a page, paged while the answer offers a token —
the probe of 2026-11-18 measured four pages of fifty, 200 videos a
region, for each configured region at category 28. Asked for, and
nothing else: each video's id, its channel's id and the token for the
next page. The Trending page left YouTube's interface in July 2025; the
endpoint outlived it.

The pages are gathered into one reading — the videos in chart order,
the position being the order — and nothing of the paging survives the
gathering: the reading holds ids only.
"""

from typing import Any

from itgraph.yt.client import YouTube

__all__ = ["FIELDS", "PAGE", "PART", "read_chart"]

Resource = dict[str, Any]

PART = "snippet"
FIELDS = "nextPageToken,items(id,snippet/channelId)"
PAGE = 50


def _next_token(body: Resource) -> str | None:
    """The token the answer offers, spent by the caller and never kept."""
    following = body.get("nextPageToken")
    return following if isinstance(following, str) and following else None


async def read_chart(
    youtube: YouTube, region: str, *, category: int
) -> Resource | None:
    """The region's chart at the category, trimmed to each video's id and
    its channel's.

    ``None`` is YouTube refusing the chart for the region — no reading
    is stored then, and the next day's run is the retry. Everything a
    request can raise, it raises: the caller isolates a region's refusal
    from the rest of the refresh.
    """
    items: list[dict[str, Any]] = []
    token: str | None = None
    while True:
        params: dict[str, str] = {
            "part": PART,
            "fields": FIELDS,
            "chart": "mostPopular",
            "regionCode": region,
            "videoCategoryId": str(category),
            "maxResults": str(PAGE),
        }
        if token is not None:
            params["pageToken"] = token
        body = await youtube.get("videos.list", params)
        if body is None:
            return None
        items.extend(body.get("items") or ())
        token = _next_token(body)
        if token is None:
            return {"items": items}
