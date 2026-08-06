"""The scoring pass: load, score, raise, report.

The one place the pure scoring meets a database. It reads baselines and
snapshots, scores in memory, and writes alerts back. It issues no
Telegram request, takes no session lease, and modifies nothing it read —
which is what makes it safe to put on a short schedule beside a running
collector.

**Replay is this same function with an earlier moment.** Not a second
implementation: a parallel scorer agrees on every case anyone bothers to
check and diverges on the one that matters. So ``now`` bounds what is
visible — a replay may not see a reading taken after the moment it is
reasoning from — and ``dry_run`` decides whether the answer is written or
only reported.

Its evidence is only as fresh as ``itgraph watch`` and ``itgraph
baselines``, and the summary says so. An alerting system's healthy state
is silence, so without those lines "nothing was unusual today" and
"nothing has been scored since the baselines went stale" read the same.
"""

import logging
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from itgraph.config import settings
from itgraph.db.alerts import raise_spikes
from itgraph.db.baselines import Baselines, load_baselines
from itgraph.db.models import Metric
from itgraph.db.session import Database
from itgraph.scoring.score import Score, Spike, score_post

__all__ = ["Reading", "SpikeSummary", "run_scoring"]

logger = logging.getLogger(__name__)

# Every counter of one snapshot, read the way `derive/metrics.py` reads a
# payload: absent stays absent. `reactions` is the one that needs saying
# — a null column means the channel publishes no reactions at all, while
# an empty object means it does and nobody has, so the sum is coalesced
# to zero *inside* the type check and left null outside it. Conflating
# the two is how a vacancy feed becomes the most-loved channel in the
# inventory.
READINGS = """
    SELECT r.channel_id,
           r.msg_id,
           (r.payload->>'grouped_id')::bigint AS album,
           (r.payload->>'date')::timestamptz  AS published,
           m.observed_at,
           m.views::float8,
           m.forwards::float8,
           m.comments::float8,
           CASE WHEN jsonb_typeof(m.reactions) = 'object' THEN coalesce(
                  (SELECT sum(entry.value::bigint)
                   FROM jsonb_each_text(m.reactions) AS entry), 0
                )::float8 END AS reactions
    FROM message_metrics m
    JOIN raw_messages r
      ON r.channel_id = m.channel_id AND r.msg_id = m.msg_id
    JOIN channels c ON c.tg_id = m.channel_id
    WHERE (r.payload->>'date')::timestamptz >= :since
      AND (r.payload->>'date')::timestamptz <= :now
      AND m.observed_at <= :now
      AND c.status = 'seed'
      AND c.is_chat = false
"""


@dataclass(frozen=True, slots=True)
class Reading:
    """One snapshot of one post, as the scorer wants it.

    ``age`` is ``observed_at`` minus the publication date — computed
    here, never inferred from which sample in the schedule this was meant
    to be. Samples are irregular by design, and a pass assuming the
    schedule was met would mis-age exactly the posts whose sampling was
    unusual.
    """

    post_key: tuple[int, int]
    age: timedelta
    values: dict[str, float | None]


@dataclass(frozen=True, slots=True)
class SpikeSummary:
    """What a pass found, and what it was able to look at.

    The last two counts are the ones that stop silence from being
    ambiguous. "No alerts from this channel" and "this channel is not
    scored" are different facts, and only the first one means quiet.
    """

    posts: int
    scored: int
    crossed: int
    raised: int
    channels_in_scope: int
    channels_with_baseline: int
    threshold: float
    replay: bool
    now: datetime
    baseline_age: timedelta | None = None
    spikes: list[Spike] = field(default_factory=list)

    def line(self) -> str:
        if self.channels_with_baseline == 0:
            return "no baselines: nothing was scored — run `itgraph baselines`"

        what = "would raise" if self.replay else "raised"
        line = (
            f"{self.posts} post(s) in the window, {self.scored} scored, "
            f"{self.crossed} past z {self.threshold:g}, "
            f"{what} {self.crossed if self.replay else self.raised}"
        )

        unscored = self.channels_in_scope - self.channels_with_baseline
        if unscored > 0:
            line += (
                f"; {unscored} of {self.channels_in_scope} channel(s) have "
                "too little history to be scored"
            )

        if self.baseline_age is not None and self.baseline_age > timedelta(
            days=settings.baseline_refresh_days
        ):
            line += (
                f"; baselines are {self.baseline_age.days}d old — "
                "run `itgraph baselines`"
            )
        return line


async def _load(
    session: AsyncSession, *, since: datetime, now: datetime
) -> list[Reading]:
    """Snapshots of posts published in the window, one per reading.

    Bounded by ``now`` on both sides, which is what makes a replay a
    replay: a pass reasoning from a past moment must not see a reading
    taken after it, or it would answer with information the live pass
    could not have had and agree with itself for the wrong reason.

    **An album is scored as its first part only.** Telegram stores each
    part as a message with its own counters, so scoring all of them would
    turn one post into up to ten near-identical alerts. The first part is
    what a ``t.me`` link resolves to and what ``alerts/run.py`` already
    collapses to. Reactions that landed on a later part are not counted,
    which understates the post rather than over-alerting it — the safe
    direction for a threshold.
    """
    rows = (
        await session.execute(text(READINGS), {"since": since, "now": now})
    ).all()

    first_part: dict[tuple[int, int], int] = {}
    for channel_id, msg_id, album, *_ in rows:
        if album is None:
            continue
        key = (channel_id, album)
        current = first_part.get(key)
        if current is None or msg_id < current:
            first_part[key] = msg_id

    readings: list[Reading] = []
    for row in rows:
        channel_id, msg_id, album, published, observed_at = row[:5]
        if album is not None and first_part[(channel_id, album)] != msg_id:
            continue
        age = observed_at - published
        if age < timedelta(0):
            # A reading predating its post is clock skew or a bad stored
            # date. A negative age passes every band test silently, which
            # is the worst way for bad data to behave.
            continue
        views, forwards, comments, reactions = row[5:]
        readings.append(
            Reading(
                post_key=(channel_id, msg_id),
                age=age,
                values={
                    Metric.VIEWS: views,
                    Metric.FORWARDS: forwards,
                    Metric.COMMENTS: comments,
                    Metric.REACTIONS: reactions,
                },
            )
        )
    return readings


def _best_per_post(
    readings: list[Reading],
    baselines: Baselines,
    *,
    metrics: tuple[Metric, ...],
) -> dict[tuple[int, int], Score]:
    """Each post's highest score, across every metric and every reading.

    **One post, one score, and therefore one alert.** Four independent
    alerts would put four messages about the most interesting post of the
    day into the chat inside an hour — the same error as one message per
    reposter, arrived at from the other side.

    Across readings as well as across metrics: a post is remarkable at
    the age it was most remarkable at, and taking only the newest reading
    would miss a post that was extraordinary at thirty minutes and merely
    good by four hours.
    """
    best: dict[tuple[int, int], Score] = {}
    for reading in readings:
        channel_id = reading.post_key[0]
        scores = score_post(
            reading.values,
            reading.age,
            {
                metric: baselines.for_channel(channel_id, metric)
                for metric in metrics
            },
            metrics=metrics,
        )
        if not scores:
            continue
        current = best.get(reading.post_key)
        if current is None or scores[0].z > current.z:
            best[reading.post_key] = scores[0]
    return best


async def run_scoring(
    database: Database,
    *,
    now: datetime | None = None,
    since: datetime | None = None,
    threshold: float | None = None,
    dry_run: bool = False,
) -> SpikeSummary:
    """Score recent posts, raise what crossed the threshold, and report.

    ``now`` is the moment reasoned from and ``dry_run`` decides whether
    anything is written; a replay is both of those and nothing else.

    With no baselines at all this raises nothing and says so, rather than
    scoring against defaults nobody chose. A default baseline would be an
    invented claim about every channel in the inventory, and it would
    fire on the largest ones first.
    """
    moment = now or datetime.now(UTC)
    window = since or moment - timedelta(hours=settings.scoring_window_hours)
    limit = threshold if threshold is not None else settings.alert_spike_z
    metrics = settings.alert_spike_metrics

    async with database.session() as session:
        baselines = await load_baselines(session)
        if baselines is None:
            summary = SpikeSummary(
                posts=0,
                scored=0,
                crossed=0,
                raised=0,
                channels_in_scope=0,
                channels_with_baseline=0,
                threshold=limit,
                replay=dry_run,
                now=moment,
            )
            logger.warning("%s", summary.line())
            return summary
        readings = await _load(session, since=window, now=moment)

    best = _best_per_post(readings, baselines, metrics=metrics)
    spikes = [
        Spike(post_key=post_key, score=score)
        for post_key, score in best.items()
        if score.z >= limit
    ]
    spikes.sort(key=lambda spike: spike.score.z, reverse=True)

    written = 0
    if spikes and not dry_run:
        async with database.session() as session:
            written = await raise_spikes(session, spikes)

    summary = SpikeSummary(
        posts=len({reading.post_key for reading in readings}),
        scored=len(best),
        crossed=len(spikes),
        raised=written,
        channels_in_scope=baselines.channels_in_scope,
        channels_with_baseline=baselines.scored_channels,
        threshold=limit,
        replay=dry_run,
        now=moment,
        baseline_age=moment - baselines.computed_at,
        spikes=spikes,
    )
    logger.info("%s", summary.line())
    return summary
