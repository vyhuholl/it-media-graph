"""Listing a channel's uploads: ``playlistItems.list``, one unit a page.

The uploads playlist is the documented way to a channel's videos, and
its id is read from the channel's stored payload — never derived from
the channel id by swapping a prefix, which works and is undocumented.

The poll loop reads one page of fifty, whatever the channel posts. The
price is the same for five or fifty, and nothing documents the
playlist's order, so the newest page is diffed against what is known
rather than trusted to start with the newest video.

The history walk pages on, one page a call, with the token each answer
gives for the next. It asks for that token and nothing more: not
``pageInfo.totalResults``, which nothing reads.

Asks for ids and publication times and nothing else: whether a video is
new, or old enough, is decided from those two, and everything else about
it comes from ``videos.list``.
"""

from dataclasses import dataclass
from datetime import datetime
from typing import Any

from itgraph.derive.yt_videos import moment_of
from itgraph.yt.client import YouTube
from itgraph.yt.errors import ApiError, ListingFailed

__all__ = [
    "FIELDS",
    "PAGE",
    "PAGED_FIELDS",
    "PART",
    "Upload",
    "UploadsPage",
    "newest_uploads",
    "uploads_page",
]

METHOD = "playlistItems.list"
PART = "contentDetails"
FIELDS = "items/contentDetails(videoId,videoPublishedAt)"
PAGED_FIELDS = f"nextPageToken,{FIELDS}"
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


async def _listing(
    youtube: YouTube, playlist_id: str, params: dict[str, str]
) -> dict[str, Any]:
    """One listing call, or ``ListingFailed`` when the playlist is not there.

    Not found or not accessible is the channel's problem, not the run's.
    Every other failure propagates as it would from any request.
    """
    try:
        body = await youtube.get(
            METHOD,
            {
                "part": PART,
                "playlistId": playlist_id,
                "maxResults": str(PAGE),
                **params,
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
    return body


async def newest_uploads(youtube: YouTube, playlist_id: str) -> list[Upload]:
    """The newest page of a channel's uploads.

    Raises ``ListingFailed`` when YouTube says the playlist is not there
    or not accessible — the channel's problem, not the pass's. Every
    other failure propagates as it would from any request.
    """
    return _uploads(await _listing(youtube, playlist_id, {"fields": FIELDS}))


@dataclass(frozen=True, slots=True)
class UploadsPage:
    """One page of a channel's uploads, and the token for the next, if any."""

    uploads: list[Upload]
    next_token: str | None


async def uploads_page(
    youtube: YouTube, playlist_id: str, token: str | None = None
) -> UploadsPage:
    """One page of a channel's uploads: the newest without a token.

    Raises ``ListingFailed`` as ``newest_uploads`` does. The token is
    used at once and never kept: how long one lives is not documented.
    """
    params = {"fields": PAGED_FIELDS}
    if token is not None:
        params["pageToken"] = token
    body = await _listing(youtube, playlist_id, params)
    following = body.get("nextPageToken")
    return UploadsPage(
        uploads=_uploads(body),
        next_token=following
        if isinstance(following, str) and following
        else None,
    )
