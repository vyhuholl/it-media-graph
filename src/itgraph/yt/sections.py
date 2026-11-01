"""Asking YouTube for a channel's sections: ``channelSections.list``.

One unit a call, and one channel a call: the method takes exactly one of
``channelId``, ``id`` (which names sections, not channels) or ``mine``,
so there is nothing to batch.

What is asked for is the channel ids each section lists, and nothing
else — not the section's type, title, playlists or position. The type is
left out on purpose. The probe of 2026-11-01 got it back lowercase
(``multiplechannels``) where the reference writes ``multipleChannels``,
and five sections of eight as ``channelsectiontypeundefined``, which the
reference does not list; ``contentDetails.channels`` is "the channel IDs
that are featured in a channel section" whatever the type, so reading
the type would only add a dependency on spelling the API contradicts.

What comes back is the response as YouTube sent it, trimmed by
``fields``. A channel YouTube answers 404 for, or whose sections it
refuses to show, is an empty list — an answer, stored, and not asked for
again before the refresh age.
"""

from typing import Any

from itgraph.yt.client import YouTube
from itgraph.yt.errors import ApiError

__all__ = ["FIELDS", "METHOD", "PARTS", "empty", "of_channel"]

Resource = dict[str, Any]

METHOD = "channelSections.list"
PARTS = "contentDetails"
FIELDS = "items(contentDetails/channels)"


def empty() -> Resource:
    """What a channel without readable sections is stored as — a new one
    each time, so no two answers share a list."""
    return {"items": []}


# The one refusal that is about the channel rather than the request:
# "The requester is not allowed to access the requested channel
# sections." Taken as no sections, so one channel cannot stop a refresh.
_FORBIDDEN = "channelSectionForbidden"


async def of_channel(youtube: YouTube, channel_id: str) -> Resource:
    """The channel's sections, trimmed to the channel ids they list."""
    try:
        body = await youtube.get(
            METHOD, {"part": PARTS, "fields": FIELDS, "channelId": channel_id}
        )
    except ApiError as exc:
        if exc.status == 403 and exc.reason == _FORBIDDEN:
            return empty()
        raise
    return empty() if body is None else body
