"""A stored YouTube video payload, read as what the poll loop needs.

Pure: a mapping in, small dataclasses out. No network, no session — the
same shape as ``derive/metrics.py``, and for the same reason: what a
payload means is a question asked again over data already stored, so
the answer has to be computable and testable without the machinery that
fetched it.

Two readings of one payload. The **facts** are what the schedule runs
on — which channel the video belongs to now, when it was published,
whether its broadcast has started. The **counters** are what a reading
records. Neither interprets anything: no format, no score. What a video
*is* — a Short, a stream, a premiere — is a classification, and under
the base Developer Policies that is derived data (III.E.4.h), which is
change 7's to make once the derived-metrics use case is accepted.
"""

from collections.abc import Mapping
from dataclasses import dataclass
from datetime import datetime
from typing import Any

__all__ = ["Counters", "VideoFacts", "counters_of", "facts_of", "moment_of"]

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
