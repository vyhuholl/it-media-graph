"""Asking YouTube for videos by id: ``videos.list``, fifty to a unit.

Any channels' videos share a request, which is what makes the poll
loop's readings cheap: a pass reading sixty videos pays two units
whichever channels they belong to.

The parts are the four the privacy policy covers, and ``fields`` holds
them to exactly its list: id, channel, title, description, publication
time, broadcast state and times, duration, and the view, like and
comment counts. Not tags, thumbnails, category or localizations, and not
``concurrentViewers``, which nothing reads.

A video YouTube does not return is absent from the result — deleted,
private, or never there. A whole request answered 404 is asked again id
by id, as ``channels.list`` is: whether one dead id can fail a batch is
not documented, and one must not cost forty-nine their reading.
"""

from collections.abc import Iterable, Sequence
from itertools import batched
from typing import Any

from itgraph.yt.client import YouTube

__all__ = ["FIELDS", "IDS_PER_CALL", "PARTS", "Resource", "by_ids"]

Resource = dict[str, Any]

METHOD = "videos.list"
PARTS = "snippet,contentDetails,liveStreamingDetails,statistics"
FIELDS = (
    "items(id,"
    "snippet(publishedAt,channelId,title,description,liveBroadcastContent),"
    "contentDetails/duration,"
    "liveStreamingDetails("
    "actualStartTime,actualEndTime,scheduledStartTime,scheduledEndTime),"
    "statistics(viewCount,likeCount,commentCount))"
)

# The most ids one request carries. The reference states no maximum for
# `videos.list`; fifty is what `channels.list` takes and what the first
# real pass will confirm or refute.
IDS_PER_CALL = 50


def _resources(body: Resource | None) -> dict[str, Resource]:
    """The videos in a response, by id. No ``items`` means none."""
    if body is None:
        return {}
    items: Iterable[Resource] = body.get("items") or ()
    return {item["id"]: item for item in items if "id" in item}


async def by_ids(youtube: YouTube, ids: Sequence[str]) -> dict[str, Resource]:
    """The videos YouTube returns for these ids, fifty to a request."""
    found: dict[str, Resource] = {}
    for batch in batched(dict.fromkeys(ids), IDS_PER_CALL):
        body = await youtube.get(
            METHOD, {"part": PARTS, "fields": FIELDS, "id": ",".join(batch)}
        )
        if body is None and len(batch) > 1:
            for one in batch:
                found |= _resources(
                    await youtube.get(
                        METHOD, {"part": PARTS, "fields": FIELDS, "id": one}
                    )
                )
            continue
        found |= _resources(body)
    return found
