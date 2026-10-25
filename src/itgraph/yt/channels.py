"""Asking YouTube for channels: by id, by handle, by legacy name.

All three are ``channels.list``, one unit a call. Ids go fifty to a call;
a handle or a legacy name goes alone, because the API takes one of each.
What comes back is the channel resource as YouTube sent it — storing it
is the caller's business, and nothing here reads beyond ``id``.
"""

from collections.abc import Iterable, Sequence
from itertools import batched
from typing import Any

from itgraph.yt.client import YouTube

__all__ = [
    "FIELDS",
    "IDS_PER_CALL",
    "PARTS",
    "Resource",
    "by_handle",
    "by_ids",
    "by_username",
]

Resource = dict[str, Any]

METHOD = "channels.list"

# What review shows, what the poll loop needs — the video count that
# triggers a listing, the uploads playlist it lists — and nothing else.
# The price is per call, not per part, so "greedy when collecting" would
# take everything; the policies allow "limited amounts" (III.E.4.d), and
# the privacy policy lists what is read. `fields` holds the request to
# that list inside the parts: a part alone would also bring thumbnails
# and localizations, and the branding, status and topic parts this used
# to ask for brought keywords, banners and flags nothing read and the
# page never mentioned. A field added here is a line added there.
PARTS = "snippet,contentDetails,statistics"
FIELDS = (
    "items(id,"
    "snippet(title,description,customUrl,country,defaultLanguage),"
    "contentDetails/relatedPlaylists/uploads,"
    "statistics(viewCount,subscriberCount,hiddenSubscriberCount,videoCount))"
)

# The most `id` takes in one call. `maxResults` is set to the same: it
# defaults to five, and whether it caps an id lookup is not documented —
# so it is made not to matter.
IDS_PER_CALL = 50


def _resources(body: Resource | None) -> dict[str, Resource]:
    """The channels in a response, by id. No ``items`` means none."""
    if body is None:
        return {}
    items: Iterable[Resource] = body.get("items") or ()
    return {item["id"]: item for item in items if "id" in item}


async def by_ids(youtube: YouTube, ids: Sequence[str]) -> dict[str, Resource]:
    """The channels YouTube returns for these ids.

    An id YouTube does not return is absent from the result — that is how
    a channel that is gone shows itself. If a whole call is answered 404,
    which the documentation lists for `id` without saying whether one
    dead id can cause it, each id in it is asked for alone: one dead
    channel must not cost forty-nine live ones their answer.
    """
    found: dict[str, Resource] = {}
    for batch in batched(dict.fromkeys(ids), IDS_PER_CALL):
        body = await youtube.get(
            METHOD,
            {
                "part": PARTS,
                "fields": FIELDS,
                "id": ",".join(batch),
                "maxResults": str(IDS_PER_CALL),
            },
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


async def by_handle(youtube: YouTube, handle: str) -> Resource | None:
    """The channel holding this handle, or ``None``."""
    body = await youtube.get(
        METHOD, {"part": PARTS, "fields": FIELDS, "forHandle": f"@{handle}"}
    )
    return next(iter(_resources(body).values()), None)


async def by_username(youtube: YouTube, name: str) -> Resource | None:
    """The channel behind a legacy ``/user/`` name, or ``None``."""
    body = await youtube.get(
        METHOD, {"part": PARTS, "fields": FIELDS, "forUsername": name}
    )
    return next(iter(_resources(body).values()), None)
