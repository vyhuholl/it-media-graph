"""What the project tells the shared monitoring, read from what is recorded.

Every series is worked out at scrape time, from the database or from
settings, and none from a counter inside another process. That is the
point of the whole module: a collector that stopped, or that is stuck
holding everything it should and using none of it, cannot report its own
absence, and the table it stopped writing to can.

Nothing here reads `raw_messages` or `message_metrics`, which would cost a
scan per scrape; the poll queue says the same thing cheaply. And nothing
here reads what YouTube said about a channel or a video. Label values are
this module's own constants and the enums', never a table's data, so no
channel, video or person can reach the store.
"""

from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from datetime import datetime

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from itgraph.config import settings
from itgraph.db import yt_alerts
from itgraph.db.alerts import failing_alerts, outstanding_alerts
from itgraph.db.models import (
    BaselineRun,
    FloodEvent,
    Platform,
    PollState,
    YtBaselineRun,
    YtPassOutcome,
    YtQuotaBucket,
    YtQuotaDay,
    YtQuotaExhausted,
    YtQuotaPool,
    YtRawChannel,
    YtWatchPass,
)
from itgraph.db.poll import queue_lag
from itgraph.db.yt_quota import pool_cap
from itgraph.schedule import in_quiet_hours
from itgraph.yt_quota import quota_day

__all__ = ["Family", "read_families", "render"]

type Labels = Mapping[str, str]


@dataclass(frozen=True, slots=True)
class Family:
    """One series, with its samples: one for each label set it has."""

    name: str
    help: str
    samples: Sequence[tuple[Labels, float]]


def _gauge(name: str, help_text: str, value: float) -> Family:
    return Family(name, help_text, [({}, value)])


def _timestamp(moment: datetime | None) -> float:
    """Seconds since the epoch, 0 for what never happened."""
    return moment.timestamp() if moment is not None else 0.0


async def _telegram(session: AsyncSession, now: datetime) -> list[Family]:
    last_poll = await session.scalar(
        select(func.max(PollState.last_polled_at))
    )
    failing = await session.scalar(
        select(func.count())
        .select_from(PollState)
        .where(PollState.consecutive_failures > 0)
    )
    lag = await queue_lag(session, now=now)
    overdue = (
        (now - lag.oldest_due_at).total_seconds()
        if lag.oldest_due_at is not None
        else 0.0
    )
    last_flood = await session.scalar(select(func.max(FloodEvent.occurred_at)))
    return [
        _gauge(
            "itgraph_telegram_last_poll_timestamp_seconds",
            "When the collector last finished polling a channel.",
            _timestamp(last_poll),
        ),
        _gauge(
            "itgraph_telegram_oldest_overdue_seconds",
            "How late the poll queue's oldest due channel is.",
            overdue,
        ),
        _gauge(
            "itgraph_telegram_failing_channels",
            "Channels whose last poll failed.",
            float(failing or 0),
        ),
        _gauge(
            "itgraph_telegram_quiet_hours",
            "1 inside the collector's quiet hours, when it polls nothing.",
            1.0 if in_quiet_hours(now) else 0.0,
        ),
        _gauge(
            "itgraph_telegram_last_flood_timestamp_seconds",
            "When a rate limit was last recorded.",
            _timestamp(last_flood),
        ),
    ]


async def _youtube(session: AsyncSession, now: datetime) -> list[Family]:
    day = quota_day(now)
    spent = {
        pool: units
        for pool, units in await session.execute(
            select(YtQuotaDay.pool, YtQuotaDay.spent).where(
                YtQuotaDay.day == day
            )
        )
    }
    exhausted = set(
        await session.scalars(
            select(YtQuotaExhausted.bucket).where(YtQuotaExhausted.day == day)
        )
    )
    last_pass = await session.scalar(
        select(func.max(YtWatchPass.finished_at)).where(
            YtWatchPass.outcome == YtPassOutcome.COMPLETE
        )
    )
    # The refresh reads every channel again before the refresh age, so the
    # oldest channel payload's age is how far behind it is. Only channels:
    # an old video, lookup or search run is old by design.
    oldest_channel = await session.scalar(
        select(func.min(YtRawChannel.fetched_at))
    )
    ages: list[tuple[Labels, float]] = []
    if oldest_channel is not None:
        ages.append(
            ({"kind": "channel"}, (now - oldest_channel).total_seconds())
        )

    return [
        Family(
            "itgraph_youtube_quota_spent_units",
            "Units booked in this quota day, by pool.",
            [
                ({"pool": pool.value}, float(spent.get(pool, 0)))
                for pool in YtQuotaPool
            ],
        ),
        Family(
            "itgraph_youtube_quota_cap_units",
            "What a pool may book in one quota day.",
            [
                ({"pool": pool.value}, float(pool_cap(pool)))
                for pool in YtQuotaPool
            ],
        ),
        Family(
            "itgraph_youtube_quota_exhausted",
            "1 when YouTube declared this quota day over for the bucket.",
            [
                ({"bucket": bucket.value}, 1.0 if bucket in exhausted else 0.0)
                for bucket in YtQuotaBucket
            ],
        ),
        _gauge(
            "itgraph_youtube_last_complete_pass_timestamp_seconds",
            "When a pass of the poll loop last completed.",
            _timestamp(last_pass),
        ),
        Family(
            "itgraph_youtube_oldest_payload_age_seconds",
            "How long ago the oldest stored channel payload was read.",
            ages,
        ),
    ]


async def _alerts(session: AsyncSession, now: datetime) -> list[Family]:
    """Each queue by its platform: what waits longest, and what fails.

    Held alerts count as waiting — a digest that never goes out is what
    the series exists to catch — and excluded ones do not, since nothing
    was meant to send them.
    """
    attempts = settings.alert_failure_report_after
    queues = {
        Platform.TELEGRAM: (
            await outstanding_alerts(session),
            await failing_alerts(session, attempts=attempts),
        ),
        Platform.YOUTUBE: (
            await yt_alerts.outstanding(session),
            await yt_alerts.failing(session, attempts=attempts),
        ),
    }
    return [
        Family(
            "itgraph_alerts_oldest_undelivered_seconds",
            "How long the oldest alert not delivered has waited, by platform.",
            [
                (
                    {"platform": platform.value},
                    (now - waiting.oldest_raised_at).total_seconds()
                    if waiting.oldest_raised_at is not None
                    else 0.0,
                )
                for platform, (waiting, _) in queues.items()
            ],
        ),
        Family(
            "itgraph_alerts_failing",
            "Undelivered alerts past the attempts that get reported, "
            "by platform.",
            [
                ({"platform": platform.value}, float(failing))
                for platform, (_, failing) in queues.items()
            ],
        ),
    ]


async def _baselines(session: AsyncSession) -> list[Family]:
    """Each platform's newest completed baseline run: when, and how many.

    A scoring pass that succeeds on baselines days old raises nothing a
    failed-job alert would see; the moment of the newest run is what does.
    Read from the run row alone — no reference, curve or factor, which the
    exporter's role cannot read.
    """
    runs = {}
    for platform, table in (
        (Platform.TELEGRAM, BaselineRun),
        (Platform.YOUTUBE, YtBaselineRun),
    ):
        runs[platform] = (
            await session.execute(
                select(
                    table.completed_at,
                    table.channels_in_scope,
                    table.channels_with_baseline,
                )
                .where(table.completed_at.is_not(None))
                .order_by(table.completed_at.desc())
                .limit(1)
            )
        ).first()
    return [
        Family(
            "itgraph_baselines_completed_timestamp_seconds",
            "When the newest completed baseline run finished, by platform; "
            "0 when none.",
            [
                (
                    {"platform": platform.value},
                    _timestamp(run[0]) if run is not None else 0.0,
                )
                for platform, run in runs.items()
            ],
        ),
        Family(
            "itgraph_baselines_channels",
            "Channels of the newest completed baseline run, in scope and "
            "with a baseline, by platform.",
            [
                (
                    {"platform": platform.value, "state": state},
                    float(run[index]) if run is not None else 0.0,
                )
                for platform, run in runs.items()
                for state, index in (("in_scope", 1), ("with_baseline", 2))
            ],
        ),
    ]


async def read_families(
    session: AsyncSession, *, now: datetime
) -> list[Family]:
    """Every series, as of ``now``. Reads and changes nothing else."""
    return [
        *await _telegram(session, now),
        *await _youtube(session, now),
        *await _alerts(session, now),
        *await _baselines(session),
    ]


def render(families: Sequence[Family]) -> str:
    """Prometheus's text format. Every series here is a gauge.

    Nothing is escaped, because no label value can need it: they are the
    constants above and the enums' values.
    """
    lines: list[str] = []
    for family in families:
        lines.append(f"# HELP {family.name} {family.help}")
        lines.append(f"# TYPE {family.name} gauge")
        for labels, value in family.samples:
            if labels:
                inner = ",".join(f'{k}="{v}"' for k, v in labels.items())
                lines.append(f"{family.name}{{{inner}}} {value}")
            else:
                lines.append(f"{family.name} {value}")
    return "\n".join(lines) + "\n"
