"""Asking YouTube which channel a linked name belongs to.

Three questions, each the narrowest request that answers it:

- a video → its channel, ``videos.list`` with the video id and channel id
  alone (``videos.OWNER``), fifty to a call;
- a handle → its channel id, ``channels.list`` ``forHandle`` with the id
  alone, one a call;
- a legacy ``/user/`` name → its channel id, ``forUsername`` the same way.

A channel id is not asked about: it is its own answer. Each function
makes one request — two or more only when a batch of videos answered 404
as a whole is asked again id by id — so the caller can commit each answer
before the next, and a run stopped by its limit or the quota keeps what
it learned. The answer is a channel id, or ``None`` for "YouTube named
none": deleted, private, never there, or a handle nobody holds.

Names in, ids out, nothing Telegram's: change 5 asks the same questions
about the names YouTube's own descriptions link.
"""

from collections.abc import Sequence

from itgraph.yt import channels, videos
from itgraph.yt.client import YouTube
from itgraph.yt.errors import YtError
from itgraph.yt_entries import CHANNEL_ID

__all__ = [
    "VIDEOS_PER_CALL",
    "UnacceptableChannelIdError",
    "channel_of_handle",
    "channel_of_legacy_name",
    "channels_of_videos",
]

VIDEOS_PER_CALL = videos.IDS_PER_CALL


class UnacceptableChannelIdError(YtError):
    """YouTube named a channel id of a shape the lookup table refuses."""

    def __init__(self, channel_id: str) -> None:
        super().__init__(
            f"YouTube named a channel id of a shape the lookup table does "
            f"not accept ({channel_id!r}). The check assumes UC and 22 "
            "characters, which YouTube never documented; relax it by "
            "migration rather than dropping the answer"
        )


def _checked(channel_id: str | None) -> str | None:
    if channel_id is not None and not CHANNEL_ID.match(channel_id):
        raise UnacceptableChannelIdError(channel_id)
    return channel_id


async def channels_of_videos(
    youtube: YouTube, video_ids: Sequence[str]
) -> dict[str, str | None]:
    """Each video's channel, or ``None`` where YouTube returns no video.

    At most ``VIDEOS_PER_CALL`` ids: one request, or one per id when the
    batch is answered 404 as a whole.
    """
    if len(video_ids) > VIDEOS_PER_CALL:
        raise ValueError(
            f"at most {VIDEOS_PER_CALL} videos to a lookup, got {len(video_ids)}"
        )
    found = await videos.by_ids(youtube, video_ids, asked=videos.OWNER)
    answers: dict[str, str | None] = {}
    for video_id in video_ids:
        snippet = (found.get(video_id) or {}).get("snippet") or {}
        owner = snippet.get("channelId")
        answers[video_id] = _checked(owner if isinstance(owner, str) else None)
    return answers


async def channel_of_handle(youtube: YouTube, handle: str) -> str | None:
    """The id of the channel holding the handle, or ``None``."""
    resource = await channels.by_handle(
        youtube,
        handle,
        parts=channels.ID_ONLY_PARTS,
        fields=channels.ID_ONLY_FIELDS,
    )
    return _checked(None if resource is None else resource["id"])


async def channel_of_legacy_name(youtube: YouTube, name: str) -> str | None:
    """The id of the channel behind the legacy name, or ``None``."""
    resource = await channels.by_username(
        youtube,
        name,
        parts=channels.ID_ONLY_PARTS,
        fields=channels.ID_ONLY_FIELDS,
    )
    return _checked(None if resource is None else resource["id"])
