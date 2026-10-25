"""Listing a channel's newest uploads: ``playlistItems.list``, one unit.

The uploads playlist is the documented way to a channel's videos, and
its id is read from the channel's stored payload — never derived from
the channel id by swapping a prefix, which works and is undocumented
(III.D.7 forbids undocumented APIs).

One page of fifty, whatever the channel posts. The price is the same
for five or fifty, and nothing documents the playlist's order, so the
newest page is diffed against what is known rather than trusted to start
with the newest video.

Asks for ids and publication times and nothing else: whether a video is
new is decided from those two, and everything else about it comes from
``videos.list``.
"""

from dataclasses import dataclass
from datetime import datetime
from typing import Any

from itgraph.derive.yt_videos import moment_of
from itgraph.yt.client import YouTube
from itgraph.yt.errors import ApiError, ListingFailed

__all__ = ["FIELDS", "PAGE", "PART", "Upload", "newest_uploads"]

METHOD = "playlistItems.list"
PART = "contentDetails"
FIELDS = "items/contentDetails(videoId,videoPublishedAt)"
PAGE = 50


@dataclass(frozen=True, slots=True)
class Upload:
    """One video in a listing: its id, and when it was published if said."""

    video_id: str
    published_at: datetime | None


def _uploads(body: dict[str, Any]) -> list[Upload]:
    found: list[Upload] = []
    for item in body.get("items") or ():
        details = (
            item.get("contentDetails") if isinstance(item, dict) else None
        )
        if not isinstance(details, dict):
            continue
        video_id = details.get("videoId")
        if isinstance(video_id, str):
            found.append(
                Upload(
                    video_id=video_id,
                    published_at=moment_of(details.get("videoPublishedAt")),
                )
            )
    return found


async def newest_uploads(youtube: YouTube, playlist_id: str) -> list[Upload]:
    """The newest page of a channel's uploads.

    Raises ``ListingFailed`` when YouTube says the playlist is not there
    or not accessible — the channel's problem, not the pass's. Every
    other failure propagates as it would from any request.
    """
    try:
        body = await youtube.get(
            METHOD,
            {
                "part": PART,
                "fields": FIELDS,
                "playlistId": playlist_id,
                "maxResults": str(PAGE),
            },
        )
    except ApiError as exc:
        if exc.status == 403:
            raise ListingFailed(
                playlist_id, f"403 {exc.reason or 'forbidden'}"
            ) from exc
        raise
    if body is None:
        raise ListingFailed(playlist_id, "404 not found")
    return _uploads(body)
