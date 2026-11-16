"""Telegram's weekly list: what took off in a week, at the alerts' threshold.

A week is Monday to Monday in the readers' zone, and a post belongs to the
week of its publication. The pass lists the last week that has ended, or
the one named, once: a week that has its list is not computed again.

**Scored as the live pass saw it.** The week's posts are read as the
scoring pass reads them (``scoring.run.load_readings``: an album by its
first part, a burst by its first message, seeds that are not chats), and
each reading is scored against the baseline run that was newest when it
was taken: the readings are cut at each run's completion and each cut
scored against its run through ``best_per_post``, on the alerts' signals
with their comment minimum. A post's score is its best across the cuts.
No run is refitted. A reading taken before any run is not scored, and is
counted. Telegram's bands end within ten hours of a post, so a late
reading scores nothing here either.

**At the alerts' threshold, one entry per channel.** A post reaches the
list when its best score reaches ``alert_spike_z`` — no floor of its own,
so the list moves when the alerts do; a channel is listed by its best
such post, and the others are counted. Channels are ranked by that score
(``scoring.weeks``), and the list holds ``weekly_list_size`` of them, or
fewer.

It makes no request and opens no session; it writes nothing but the list
tables, and with a preview nothing at all.
"""

import logging
from collections.abc import Sequence
from dataclasses import dataclass, field
from datetime import UTC, date, datetime

from itgraph.config import settings
from itgraph.db.baselines import Baselines, RunInForce, load_baselines
from itgraph.db.baselines import runs_in_force as find_runs
from itgraph.db.models import Metric
from itgraph.db.session import Database
from itgraph.db.weekly import NewEntry, NewList, store_week, week_has_list
from itgraph.scoring.run import load_readings
from itgraph.scoring.score import Baseline, Best, Reading, best_per_post
from itgraph.scoring.weeks import (
    Ranked,
    check_week,
    last_ended_week,
    merge_best,
    rank_week,
    week_bounds,
)

__all__ = ["WeeklySummary", "run_weekly", "score_week"]

logger = logging.getLogger(__name__)

type PostKey = tuple[int, int]


@dataclass(frozen=True, slots=True)
class Scored:
    """A week's posts scored: each one's best, and what was left out."""

    best: dict[PostKey, Best]
    posts: int
    unscored_readings: int


def score_week(
    readings: Sequence[Reading[int, int]],
    runs: Sequence[tuple[RunInForce, Baselines]],
    *,
    until: datetime,
    threshold: float,
    comments_min: int,
) -> Scored:
    """Each post's best, every reading against the run in force when taken.

    ``runs`` oldest first. A run is in force from its completion to the
    next one's, the last up to ``until``; readings before the first are
    not scored, and are counted.
    """
    taken = [reading for reading in readings if reading.observed_at < until]
    first = runs[0][0].completed_at if runs else until
    unscored = sum(reading.observed_at < first for reading in taken)

    best: dict[PostKey, Best] = {}
    for index, (run, baselines) in enumerate(runs):
        ends = (
            runs[index + 1][0].completed_at if index + 1 < len(runs) else until
        )
        cut = [
            reading
            for reading in taken
            if run.completed_at <= reading.observed_at < min(ends, until)
        ]
        if not cut:
            continue

        def baseline_for(
            reading: Reading[int, int],
            metric: str,
            baselines: Baselines = baselines,
        ) -> Baseline | None:
            return baselines.for_channel(reading.post_key[0], Metric(metric))

        for key, found in best_per_post(
            cut,
            baseline_for,
            metrics=tuple(settings.alert_spike_metrics),
            threshold=threshold,
            minimum={Metric.COMMENTS: comments_min},
        ).items():
            best[key] = merge_best(found, best.get(key))

    return Scored(
        best=best,
        posts=len({reading.post_key for reading in taken}),
        unscored_readings=unscored,
    )


@dataclass(frozen=True, slots=True)
class WeeklySummary:
    """What a run of the pass computed, and whether it wrote it."""

    week_start: date
    now: datetime
    threshold: float
    size: int
    posts: int = 0
    posts_scored: int = 0
    posts_above: int = 0
    unscored_readings: int = 0
    # The ids of the baseline runs the readings were scored against,
    # oldest first.
    runs: tuple[int, ...] = ()
    standings: list[Ranked[int, int]] = field(default_factory=list)
    entries: list[NewEntry] = field(default_factory=list)
    # Whether the list was stored: False for a preview, and for a week
    # that already had its list.
    stored: bool = False
    already: bool = False

    @property
    def channels_above(self) -> int:
        return len(self.standings)

    def line(self) -> str:
        if self.already:
            return (
                f"the week of {self.week_start} already has its list; "
                "nothing written"
            )
        line = (
            f"week of {self.week_start}: {len(self.entries)} listed of "
            f"{self.channels_above} channel(s) and {self.posts_above} "
            f"post(s) at z {self.threshold:g} or more; {self.posts_scored} "
            f"of {self.posts} post(s) scored, against {len(self.runs)} "
            "baseline run(s)"
        )
        if self.runs:
            line += " (" + ", ".join(f"#{run}" for run in self.runs) + ")"
        if self.unscored_readings:
            line += (
                f"; {self.unscored_readings} reading(s) taken before any "
                "baseline run, not scored"
            )
        if not self.stored:
            line += "; preview, nothing written"
        return line

    def preview(self) -> list[str]:
        """The list by ids, one line an entry."""
        if not self.entries:
            return ["  nothing at or above the threshold"]
        return [
            f"  {entry.rank:>2}. z {entry.value:5.2f} "
            f"{entry.metric.value:<9} {entry.channel_id} {entry.msg_id}"
            + (f" +{entry.others}" if entry.others else "")
            for entry in self.entries
        ]


def _entries(
    standings: Sequence[Ranked[int, int]], *, size: int
) -> list[NewEntry]:
    return [
        NewEntry(
            rank=rank,
            channel_id=standing.channel,
            msg_id=standing.item,
            scored_at=standing.best.scored_at,
            metric=Metric(standing.best.score.metric),
            value=standing.best.score.z,
            observed=standing.best.score.observed,
            expected=standing.best.score.expected,
            others=standing.others,
        )
        for rank, standing in enumerate(standings[:size], start=1)
    ]


async def run_weekly(
    database: Database,
    *,
    week: date | None = None,
    now: datetime | None = None,
    preview: bool = False,
) -> WeeklySummary:
    """Compute a week's list, store it unless previewing, and report.

    Raises ``WeekRefused`` for a week that cannot be listed, before
    anything is read.
    """
    moment = now or datetime.now(UTC)
    zone = settings.watch_timezone
    week_start = (
        week if week is not None else last_ended_week(moment, zone=zone)
    )
    check_week(week_start, moment=moment, zone=zone)
    threshold = settings.alert_spike_z
    size = settings.weekly_list_size
    comments_min = settings.alert_spike_comments_min
    start, end = week_bounds(week_start, zone=zone)

    async with database.session() as session:
        if not preview and await week_has_list(session, week_start):
            summary = WeeklySummary(
                week_start=week_start,
                now=moment,
                threshold=threshold,
                size=size,
                already=True,
            )
            logger.info("%s", summary.line())
            return summary
        loaded = await load_readings(session, since=start, now=moment)
        runs: list[tuple[RunInForce, Baselines]] = []
        for run in await find_runs(session, since=start, until=moment):
            baselines = await load_baselines(session, run_id=run.run_id)
            if baselines is not None:
                runs.append((run, baselines))

    readings = [
        reading
        for reading in loaded
        if reading.observed_at - reading.age < end
    ]
    scored = score_week(
        readings,
        runs,
        until=moment,
        threshold=threshold,
        comments_min=comments_min,
    )
    standings = rank_week(scored.best, floor=threshold)
    entries = _entries(standings, size=size)

    stored = False
    if not preview:
        async with database.session() as session:
            stored = await store_week(
                session,
                NewList(
                    week_start=week_start,
                    computed_at=moment,
                    threshold=threshold,
                    size=size,
                    comments_min=comments_min,
                    posts_scored=len(scored.best),
                    channels_above=len(standings),
                    entries=tuple(entries),
                ),
            )

    summary = WeeklySummary(
        week_start=week_start,
        now=moment,
        threshold=threshold,
        size=size,
        posts=scored.posts,
        posts_scored=len(scored.best),
        posts_above=sum(
            found.score.z >= threshold for found in scored.best.values()
        ),
        unscored_readings=scored.unscored_readings,
        runs=tuple(run.run_id for run, _ in runs),
        standings=standings,
        entries=entries,
        stored=stored,
        already=not preview and not stored,
    )
    logger.info("%s", summary.line())
    return summary
