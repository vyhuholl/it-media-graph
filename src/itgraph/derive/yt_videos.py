"""A stored YouTube video payload, read as what the poll loop needs.

Pure: a mapping in, small dataclasses out. No network, no session — the
same shape as ``derive/metrics.py``, and for the same reason: what a
payload means is a question asked again over data already stored, so
the answer has to be computable and testable without the machinery that
fetched it.

Two readings of one payload. The **facts** are what the schedule runs
on — which channel the video belongs to now, when it was published,
whether its broadcast has started. The **counters** are what a reading
records. Neither interprets anything.

A third reading belongs to scoring alone: the **format** a video is scored
in, short-form or long-form by its duration, or none for a broadcast. It is
worked out here whenever it is asked for and never stored per video.
"""

import re
from collections.abc import Mapping
from dataclasses import dataclass
from datetime import datetime
from typing import Any

from itgraph.db.models import YtVideoFormat

__all__ = [
    "Counters",
    "VideoFacts",
    "counters_of",
    "duration_of",
    "facts_of",
    "format_of",
    "moment_of",
]

# `snippet.liveBroadcastContent`, as the API reference lists it.
NONE = "none"
LIVE = "live"
UPCOMING = "upcoming"
BROADCAST_STATES = frozenset({NONE, LIVE, UPCOMING})


@dataclass(frozen=True, slots=True)
class Counters:
    """One reading of one video. ``None`` is absent, never zero.

    YouTube sends counts as strings, and leaves a counter out where the
    video does not show it — likes hidden, comments off, as far as
    anyone can tell; the reference does not say. A video nobody has
    liked and one whose likes cannot be seen are different facts, and a
    baseline built over the second as if it were the first is a
    baseline of zeros.
    """

    views: int | None = None
    likes: int | None = None
    comments: int | None = None


@dataclass(frozen=True, slots=True)
class VideoFacts:
    """What the schedule needs from a video, as YouTube last described it.

    ``channel_id`` can change: the reference says a video may be
    reassigned to another channel. ``broadcast`` is ``none`` for a
    video with no broadcast and for one whose broadcast is over.
    """

    channel_id: str | None
    published_at: datetime | None
    actual_start: datetime | None
    scheduled_start: datetime | None
    broadcast: str

    @property
    def upcoming(self) -> bool:
        """A broadcast that has not started: tracked, not read."""
        return self.broadcast == UPCOMING


def moment_of(value: Any) -> datetime | None:
    """An ISO 8601 moment from the payload, or ``None`` if it is not one."""
    if not isinstance(value, str):
        return None
    try:
        moment = datetime.fromisoformat(value)
    except ValueError:
        return None
    return moment if moment.tzinfo is not None else None


def _section(payload: Mapping[str, Any], name: str) -> Mapping[str, Any]:
    section = payload.get(name)
    return section if isinstance(section, Mapping) else {}


def facts_of(payload: Mapping[str, Any]) -> VideoFacts:
    """The facts of one stored video payload."""
    snippet = _section(payload, "snippet")
    live = _section(payload, "liveStreamingDetails")
    channel = snippet.get("channelId")
    broadcast = snippet.get("liveBroadcastContent")
    return VideoFacts(
        channel_id=channel if isinstance(channel, str) else None,
        published_at=moment_of(snippet.get("publishedAt")),
        actual_start=moment_of(live.get("actualStartTime")),
        scheduled_start=moment_of(live.get("scheduledStartTime")),
        broadcast=broadcast if broadcast in BROADCAST_STATES else NONE,
    )


def _count(value: Any) -> int | None:
    """A counter as YouTube sends it — a string of digits — or ``None``.

    An int is taken too, in case the shape changes. ``bool`` is refused
    explicitly: it is an ``int`` to Python and never a count.
    """
    if isinstance(value, bool):
        return None
    if isinstance(value, int):
        return value if value >= 0 else None
    if isinstance(value, str) and value.isascii() and value.isdigit():
        return int(value)
    return None


def counters_of(payload: Mapping[str, Any]) -> Counters:
    """The three counters of one stored video payload."""
    statistics = _section(payload, "statistics")
    return Counters(
        views=_count(statistics.get("viewCount")),
        likes=_count(statistics.get("likeCount")),
        comments=_count(statistics.get("commentCount")),
    )


# ISO 8601 durations as `contentDetails.duration` gives them: `PT12M30S`,
# `PT1H`, `P1DT2H3M`, and `P0D` for what has no length yet. Weeks are
# accepted though the reference shows none.
_DURATION = re.compile(
    r"P(?:(?P<w>\d+)W)?(?:(?P<d>\d+)D)?"
    r"(?:T(?:(?P<h>\d+)H)?(?:(?P<m>\d+)M)?(?:(?P<s>\d+)S)?)?"
)
_SECONDS = {"w": 604800, "d": 86400, "h": 3600, "m": 60, "s": 1}


def duration_of(payload: Mapping[str, Any]) -> int | None:
    """A video's length in seconds, or ``None`` where it cannot be read.

    A length of zero is no length: it is what a broadcast yet to start or a
    video still processing reports, and a video of zero seconds is not one
    anybody watched.
    """
    value = _section(payload, "contentDetails").get("duration")
    if not isinstance(value, str):
        return None
    match = _DURATION.fullmatch(value)
    if match is None or not any(match.groupdict().values()):
        return None
    seconds = sum(
        int(amount) * _SECONDS[unit]
        for unit, amount in match.groupdict().items()
        if amount is not None
    )
    return seconds or None


def format_of(
    payload: Mapping[str, Any], *, short_max_seconds: int
) -> YtVideoFormat | None:
    """The population a video is scored in, or ``None`` if it is not scored.

    A video with broadcast details — a stream, or a premiere, which YouTube
    runs as one — is in neither: it is not scored. Nor is one whose length
    cannot be read. Otherwise short-form up to ``short_max_seconds``, the
    limit of a Short, and long-form past it. No flag says a video is a
    Short; a two-minute video that is not one is short-form either way.
    """
    if isinstance(payload.get("liveStreamingDetails"), Mapping):
        return None
    seconds = duration_of(payload)
    if seconds is None:
        return None
    if seconds <= short_max_seconds:
        return YtVideoFormat.SHORT
    return YtVideoFormat.LONG
