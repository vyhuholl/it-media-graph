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

Past uploads — what the history walk finds and the refresh keeps — are
asked for in the same shape less the counters until the derived-metrics
use case is accepted. A video linked from elsewhere is asked for its
channel id alone (``OWNER``). Before then nothing may score them, so a count
would be kept for nobody; and the same unit buys the same payload
either way.
"""

from collections.abc import Iterable, Sequence
from dataclasses import dataclass
from itertools import batched
from typing import Any

from itgraph import yt_policy
from itgraph.yt.client import YouTube

__all__ = [
    "FIELDS",
    "IDS_PER_CALL",
    "LOOP",
    "OWNER",
    "PARTS",
    "PAST_UPLOADS",
    "Asked",
    "Resource",
    "by_ids",
    "for_past_uploads",
]

Resource = dict[str, Any]

METHOD = "videos.list"

_SNIPPET = (
    "snippet(publishedAt,channelId,title,description,liveBroadcastContent)"
)
_LIVE = (
    "liveStreamingDetails("
    "actualStartTime,actualEndTime,scheduledStartTime,scheduledEndTime)"
)
_COUNTERS = "statistics(viewCount,likeCount,commentCount)"

PARTS = "snippet,contentDetails,liveStreamingDetails,statistics"
FIELDS = f"items(id,{_SNIPPET},contentDetails/duration,{_LIVE},{_COUNTERS})"


@dataclass(frozen=True, slots=True)
class Asked:
    """The parts and fields one kind of request names."""

    parts: str
    fields: str


# The poll loop's request: everything the privacy policy lists.
LOOP = Asked(parts=PARTS, fields=FIELDS)
# A past upload's before acceptance: the same, less the counters.
PAST_UPLOADS = Asked(
    parts="snippet,contentDetails,liveStreamingDetails",
    fields=f"items(id,{_SNIPPET},contentDetails/duration,{_LIVE})",
)


# A linked video's lookup: which channel it belongs to, and nothing else.
# The part is the one `channelId` lives in; `fields` keeps the title,
# description and everything else in it out of the answer, so a video of
# a channel outside the inventory costs the privacy page two fields.
OWNER = Asked(parts="snippet", fields="items(id,snippet/channelId)")


def for_past_uploads() -> Asked:
    """How a past upload is asked for: without counters until acceptance."""
    if yt_policy.DERIVED_METRICS_ACCEPTED_ON is None:
        return PAST_UPLOADS
    return LOOP


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


async def by_ids(
    youtube: YouTube, ids: Sequence[str], *, asked: Asked = LOOP
) -> dict[str, Resource]:
    """The videos YouTube returns for these ids, fifty to a request."""
    shape = {"part": asked.parts, "fields": asked.fields}
    found: dict[str, Resource] = {}
    for batch in batched(dict.fromkeys(ids), IDS_PER_CALL):
        body = await youtube.get(METHOD, {**shape, "id": ",".join(batch)})
        if body is None and len(batch) > 1:
            for one in batch:
                found |= _resources(
                    await youtube.get(METHOD, {**shape, "id": one})
                )
            continue
        found |= _resources(body)
    return found
