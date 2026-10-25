"""Stand-ins for the YouTube Data API. Nothing here touches a socket.

Every id, handle and title is invented. Ids carry `fake` in them so no
one reading a failing test can mistake one for a channel that exists;
the key is spelled out in words, so gitleaks and a reviewer can both
tell it from a real one (those start `AIza`).
"""

from typing import Any

import httpx

# Not a key's shape on purpose; see the module docstring.
API_KEY = "test-youtube-api-key"


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
) -> dict[str, Any]:
    """A channel resource in the shape ``channels.list`` returns.

    Only the fields this project reads, under the parts it requests. The
    handle comes back as ``customUrl`` with its ``@``, which is the
    assumption design.md records — the first real request settles it.
    """
    handle = handle if handle is not None else f"fakehandle{number}"
    snippet: dict[str, Any] = {
        "title": title if title is not None else f"Fake Channel {number}",
        "description": "An invented channel for tests.",
        "customUrl": f"@{handle}",
        "publishedAt": "2020-01-01T00:00:00Z",
    }
    if language is not None:
        snippet["defaultLanguage"] = language
    if country is not None:
        snippet["country"] = country
    return {
        "kind": "youtube#channel",
        "etag": f"fake-etag-{number}",
        "id": channel_id(number),
        "snippet": snippet,
        "contentDetails": {
            "relatedPlaylists": {"uploads": "UU" + channel_id(number)[2:]}
        },
        "statistics": {
            "viewCount": "100000",
            "subscriberCount": str(subscribers),
            "hiddenSubscriberCount": False,
            "videoCount": "42",
        },
        "brandingSettings": {"channel": {"title": snippet["title"]}},
        "status": {"privacyStatus": "public"},
        "topicDetails": {"topicCategories": []},
    }


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
    """``channels.list`` as far as this project calls it, over MockTransport.

    Holds channel resources by id, answers by ``id``, ``forHandle`` and
    ``forUsername``, and records every request it saw. ``queued`` answers
    come first, in order — that is how a test makes YouTube fail.

    ``batch_404`` makes an id call that names a missing channel answer 404
    for the whole call instead of leaving the channel out: the documented
    error whose scope the documentation does not state.
    """

    def __init__(
        self,
        *resources: dict[str, Any],
        legacy: dict[str, str] | None = None,
        batch_404: bool = False,
    ) -> None:
        self.channels = {resource["id"]: resource for resource in resources}
        self.legacy = {
            name.lower(): id_ for name, id_ in (legacy or {}).items()
        }
        self.batch_404 = batch_404
        self.queued: list[httpx.Response] = []
        self.requests: list[httpx.Request] = []

    def handler(self, request: httpx.Request) -> httpx.Response:
        self.requests.append(request)
        if self.queued:
            return self.queued.pop(0)

        params = request.url.params
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
