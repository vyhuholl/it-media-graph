"""Stand-ins for the YouTube Data API. Nothing here touches a socket.

Every id, handle and title is invented. Ids carry `fake` in them so no
one reading a failing test can mistake one for a channel that exists;
the key is spelled out in words, so gitleaks and a reviewer can both
tell it from a real one (those start `AIza`).
"""

from datetime import datetime
from typing import Any

import httpx

# Not a key's shape on purpose; see the module docstring.
API_KEY = "test-youtube-api-key"


def stamp(moment: datetime) -> str:
    """A moment as YouTube writes one: UTC, seconds, a trailing ``Z``."""
    return moment.strftime("%Y-%m-%dT%H:%M:%SZ")


def video_id(number: int) -> str:
    """A video id of the right shape — eleven characters — that no video has."""
    return f"fakevid{number:04d}"


def video_resource(
    number: int,
    *,
    published: datetime,
    channel: int = 1,
    title: str | None = None,
    views: int | None = 100,
    likes: int | None = 10,
    comments: int | None = 3,
    broadcast: str = "none",
    live_started: datetime | None = None,
    scheduled: datetime | None = None,
) -> dict[str, Any]:
    """A video resource in the shape ``videos.list`` returns under our fields.

    A counter given as ``None`` is left out of ``statistics``, which is
    how a hidden like count is expected to arrive — expected, because the
    reference does not say. Broadcast details appear only for a video
    that has, had or will have a broadcast, as the reference says.
    """
    resource: dict[str, Any] = {
        "id": video_id(number),
        "snippet": {
            "publishedAt": stamp(published),
            "channelId": channel_id(channel),
            "title": title if title is not None else f"Fake Video {number}",
            "description": "An invented video for tests.",
            "liveBroadcastContent": broadcast,
        },
        "contentDetails": {"duration": "PT12M30S"},
        "statistics": {
            name: str(value)
            for name, value in (
                ("viewCount", views),
                ("likeCount", likes),
                ("commentCount", comments),
            )
            if value is not None
        },
    }
    live: dict[str, str] = {}
    if scheduled is not None:
        live["scheduledStartTime"] = stamp(scheduled)
    if live_started is not None:
        live["actualStartTime"] = stamp(live_started)
    if live:
        resource["liveStreamingDetails"] = live
    return resource


def channel_id(number: int) -> str:
    """A channel id of the right shape that no channel has.

    ``UC`` and twenty-two characters, which is what the inventory's
    check holds an id to.
    """
    return f"UCfake_channel_{number:09d}"


def channel_resource(
    number: int,
    *,
    handle: str | None = None,
    title: str | None = None,
    subscribers: int = 1200,
    language: str | None = None,
    country: str | None = None,
    videos: int = 42,
) -> dict[str, Any]:
    """A channel resource in the shape ``channels.list`` returns.

    Exactly the fields requested, as ``yt/channels.py`` names them in
    ``fields`` — which is also exactly what the privacy policy lists.
    The handle comes back as ``customUrl`` with its ``@``, which is the
    assumption change 1 recorded.
    """
    handle = handle if handle is not None else f"fakehandle{number}"
    snippet: dict[str, Any] = {
        "title": title if title is not None else f"Fake Channel {number}",
        "description": "An invented channel for tests.",
        "customUrl": f"@{handle}",
    }
    if language is not None:
        snippet["defaultLanguage"] = language
    if country is not None:
        snippet["country"] = country
    return {
        "id": channel_id(number),
        "snippet": snippet,
        "contentDetails": {
            "relatedPlaylists": {"uploads": uploads_id(number)}
        },
        "statistics": {
            "viewCount": "100000",
            "subscriberCount": str(subscribers),
            "hiddenSubscriberCount": False,
            "videoCount": str(videos),
        },
    }


def uploads_id(number: int) -> str:
    """The uploads playlist a fake channel's payload names.

    Built with the undocumented UC→UU substitution only because a fake
    needs some id; the code under test never derives one, it reads the
    payload.
    """
    return "UU" + channel_id(number)[2:]


def upload_item(number: int, *, published: datetime | None) -> dict[str, Any]:
    """One item of an uploads listing, in the shape our ``fields`` give."""
    details: dict[str, str] = {"videoId": video_id(number)}
    if published is not None:
        details["videoPublishedAt"] = stamp(published)
    return {"contentDetails": details}


def channel_list(*resources: dict[str, Any]) -> dict[str, Any]:
    """A ``channels.list`` response holding the given resources.

    With none, ``items`` is left out altogether — one of the two shapes
    "nothing matched" may take, and the one the client must not trip on.
    """
    body: dict[str, Any] = {
        "kind": "youtube#channelListResponse",
        "etag": "fake-etag-list",
        "pageInfo": {
            "totalResults": len(resources),
            "resultsPerPage": 5,
        },
    }
    if resources:
        body["items"] = list(resources)
    return body


def error_body(code: int, reason: str, message: str = "") -> dict[str, Any]:
    """Google's standard error body, as the YouTube API is expected to send.

    Not shown on YouTube's own errors page; this is the general Google
    API shape, which is why the client reads it defensively.
    """
    return {
        "error": {
            "code": code,
            "message": message or reason,
            "errors": [
                {
                    "message": message or reason,
                    "domain": "youtube.quota"
                    if reason == "quotaExceeded"
                    else "global",
                    "reason": reason,
                }
            ],
        }
    }


class FakeYouTube:
    """The three methods this project calls, over MockTransport.

    Holds channel resources by id and answers ``channels.list`` by
    ``id``, ``forHandle`` and ``forUsername``; holds uploads listings by
    playlist id and video resources by id for ``playlistItems.list`` and
    ``videos.list``. Records every request it saw. ``queued`` answers
    come first, in order — that is how a test makes YouTube fail.

    ``batch_404`` makes an id call that names a missing channel or video
    answer 404 for the whole call instead of leaving it out: the
    documented error whose scope the documentation does not state.
    """

    def __init__(
        self,
        *resources: dict[str, Any],
        legacy: dict[str, str] | None = None,
        batch_404: bool = False,
        uploads: dict[str, list[dict[str, Any]]] | None = None,
        videos: list[dict[str, Any]] | None = None,
    ) -> None:
        self.channels = {resource["id"]: resource for resource in resources}
        self.legacy = {
            name.lower(): id_ for name, id_ in (legacy or {}).items()
        }
        self.batch_404 = batch_404
        self.uploads = dict(uploads or {})
        self.videos = {video["id"]: video for video in videos or []}
        self.queued: list[httpx.Response] = []
        self.requests: list[httpx.Request] = []

    def method(self, request: httpx.Request) -> str:
        """``channels``, ``playlistItems`` or ``videos``: the path's last part."""
        return request.url.path.rsplit("/", 1)[-1]

    def calls(self, method: str) -> list[dict[str, str]]:
        """What each request to one method asked for, in order."""
        return [
            dict(request.url.params)
            for request in self.requests
            if self.method(request) == method
        ]

    def handler(self, request: httpx.Request) -> httpx.Response:
        self.requests.append(request)
        if self.queued:
            return self.queued.pop(0)

        params = request.url.params
        if self.method(request) == "playlistItems":
            playlist = params.get("playlistId", "")
            if playlist not in self.uploads:
                return httpx.Response(
                    404, json=error_body(404, "playlistNotFound")
                )
            limit = int(params.get("maxResults", "5"))
            return httpx.Response(
                200, json={"items": self.uploads[playlist][:limit]}
            )
        if self.method(request) == "videos":
            ids = params["id"].split(",")
            found = [self.videos[id_] for id_ in ids if id_ in self.videos]
            if self.batch_404 and len(found) < len(ids):
                return httpx.Response(
                    404, json=error_body(404, "videoNotFound")
                )
            body: dict[str, Any] = {"items": found} if found else {}
            return httpx.Response(200, json=body)
        if "id" in params:
            ids = params["id"].split(",")
            found = [self.channels[id_] for id_ in ids if id_ in self.channels]
            if self.batch_404 and len(found) < len(ids):
                return httpx.Response(
                    404, json=error_body(404, "channelNotFound")
                )
            return httpx.Response(200, json=channel_list(*found))
        if "forHandle" in params:
            wanted = "@" + params["forHandle"].removeprefix("@").lower()
            found = [
                resource
                for resource in self.channels.values()
                if resource["snippet"]["customUrl"].lower() == wanted
            ]
            return httpx.Response(200, json=channel_list(*found))
        if "forUsername" in params:
            id_ = self.legacy.get(params["forUsername"].lower())
            found = [self.channels[id_]] if id_ in self.channels else []
            return httpx.Response(200, json=channel_list(*found))
        return httpx.Response(400, json=error_body(400, "invalidCriteria"))

    def transport(self) -> httpx.MockTransport:
        return httpx.MockTransport(self.handler)

    def params(self) -> list[dict[str, str]]:
        """What each request asked for, in order."""
        return [dict(request.url.params) for request in self.requests]


async def no_sleep(seconds: float) -> None:
    """Backoff that does not wait: the tests count attempts, not seconds."""


def use_key(monkeypatch: Any) -> None:
    """Configure the fake key, the way `.env` would."""
    from pydantic import SecretStr

    from itgraph.config import settings

    monkeypatch.setattr(settings, "youtube_api_key", SecretStr(API_KEY))
