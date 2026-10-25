"""What counts as one thing: one post, and one event per channel.

Pure functions over plain values, the same shape as ``curves.py`` and
``score.py``: no database, no clock, every input passed in. The scoring
pass and its replay both call these, so they are tested once and agree.

Two collapses, at two levels, and they answer different questions.

**A burst is one post.** Telegram groups an album's parts, but a post
published as text and then each video sent separately is several
messages nothing groups. They are read together and their counters move
together, so scored one by one they are one post alerting several times.

**A channel's spikes inside a window are one event.** When a whole
channel's audience grows at once — an ad, a broadcast, bought views —
every recent post crosses together. Per post the score is right; the
unit is wrong. The first spike becomes the *head* and the rest fold into
it, so the reader gets one message saying the whole channel moved, rather
than seven saying seven posts did.
"""

from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass
from datetime import datetime, timedelta

from itgraph.scoring.score import Spike

__all__ = ["Head", "Placed", "burst_heads", "fold"]


def burst_heads(
    posts: Iterable[tuple[tuple[int, int], datetime]], *, gap: timedelta
) -> dict[tuple[int, int], tuple[int, int]]:
    """Each post's burst, named by the burst's first message.

    A message joins the burst of the one before it when it was published
    less than ``gap`` after it, so a burst is a chain: four messages 20 s
    apart are one post spanning a minute. Chained rather than measured
    from the first message because that is how such a post is made — one
    upload after another — and the gaps, not the total, are what tell it
    from two posts.

    Ordered by publication and then by id, so messages sharing a second
    resolve the same way every time. A post may be passed once per
    reading; repeats are harmless. A ``gap`` of zero joins nothing.
    """
    by_channel: dict[int, set[tuple[datetime, int]]] = {}
    for (channel, msg), published in posts:
        by_channel.setdefault(channel, set()).add((published, msg))

    heads: dict[tuple[int, int], tuple[int, int]] = {}
    for channel, entries in by_channel.items():
        head = 0
        previous: datetime | None = None
        for published, msg in sorted(entries):
            if previous is None or published - previous >= gap:
                head = msg
            heads[(channel, msg)] = (channel, head)
            previous = published
    return heads


@dataclass(frozen=True, slots=True)
class Head:
    """A head already in the queue, which later spikes may fold into.

    Separate from a ``Spike`` because it is a different fact: this one
    was raised by an earlier pass, has an id, and its window opened when
    it was *raised* — the only moment the queue recorded. A head found in
    the same pass is the ``Spike`` itself, and its window opens when it
    crossed. The two differ by at most one pass interval.
    """

    alert_id: int
    post_key: tuple[int, int]
    raised_at: datetime


@dataclass(frozen=True, slots=True)
class Placed:
    """A spike, and the head it folds into — or ``None`` if it is one."""

    spike: Spike
    into: Spike | Head | None


def fold(
    spikes: Sequence[Spike],
    *,
    heads: Mapping[int, Head],
    window: timedelta,
) -> list[Placed]:
    """Which spikes head an event, and which fold into one.

    A spike folds into its channel's head if it crossed less than
    ``window`` after the head opened; otherwise it opens a new event.
    ``heads`` is each channel's newest head already in the queue — empty
    for a replay, which reasons from no queue at all.

    **The window is anchored at the head and does not slide.** Measured
    from the channel's *last* crossing instead, daily boosts that are
    never more than a day apart would chain into one event forever. Fixed,
    the guarantee is plain: one direct message per channel per window.

    Spikes are taken in the order they crossed, and among those that
    crossed at one reading, highest score first: the head is the earliest
    post to move, and the most remarkable of those that moved together.
    """
    open_events: dict[int, tuple[datetime, Spike | Head]] = {
        channel: (head.raised_at, head) for channel, head in heads.items()
    }
    placed: list[Placed] = []
    for spike in sorted(
        spikes,
        key=lambda entry: (entry.crossed_at, -entry.score.z, entry.post_key),
    ):
        channel = spike.post_key[0]
        event = open_events.get(channel)
        if event is not None and spike.crossed_at - event[0] < window:
            placed.append(Placed(spike=spike, into=event[1]))
            continue
        open_events[channel] = (spike.crossed_at, spike)
        placed.append(Placed(spike=spike, into=None))
    return placed
