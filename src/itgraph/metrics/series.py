"""What the project tells the shared monitoring, read from what is recorded.

Every series is worked out at scrape time, from the database or from
settings, and none from a counter inside another process. That is the
point of the whole module: a collector that stopped, or that is stuck
holding everything it should and using none of it, cannot report its own
absence, and the table it stopped writing to can.

Nothing here reads `raw_messages` or `message_metrics`, which would cost a
scan per scrape; the poll queue says the same thing cheaply. And nothing
here reads API data: of the YouTube tables it asks only when each row was
fetched, and the database role it connects as can ask nothing else. Label
values are this module's own constants and the enums', never a table's
data, so no channel, video or person can reach the store.
"""

from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from datetime import datetime

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import InstrumentedAttribute

from itgraph import yt_policy
from itgraph.config import settings
from itgraph.db import yt_alerts
from itgraph.db.alerts import failing_alerts, outstanding_alerts
from itgraph.db.models import (
    FloodEvent,
    Platform,
    PollState,
    YtPassOutcome,
    YtQuotaBucket,
    YtQuotaDay,
    YtQuotaExhausted,
    YtQuotaPool,
    YtRawChannel,
    YtRawChannelSections,
    YtRawLookup,
    YtRawVideo,
    YtRawVideoStat,
    YtSearchRun,
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


# Each kind of stored API data and the column saying when it was fetched:
# what the database role can read of those tables, and all it can.
_FETCHED: Mapping[str, InstrumentedAttribute[datetime]] = {
    "channel": YtRawChannel.fetched_at,
    "video": YtRawVideo.fetched_at,
    "lookup": YtRawLookup.fetched_at,
    "featured": YtRawChannelSections.fetched_at,
    "search": YtSearchRun.asked_at,
}


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

    # Readings keep 30 days only until acceptance is recorded; from then
    # 36 months is lawful, and their age says nothing a person should act on.
    fetched = dict(_FETCHED)
    if yt_policy.DERIVED_METRICS_ACCEPTED_ON is None:
        fetched["reading"] = YtRawVideoStat.observed_at

    ages: list[tuple[Labels, float]] = []
    for kind, column in fetched.items():
        oldest = await session.scalar(select(func.min(column)))
        if oldest is not None:
            ages.append(({"kind": kind}, (now - oldest).total_seconds()))

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
            "How long ago the oldest stored API data of each kind was read.",
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


async def read_families(
    session: AsyncSession, *, now: datetime
) -> list[Family]:
    """Every series, as of ``now``. Reads and changes nothing else."""
    return [
        *await _telegram(session, now),
        *await _youtube(session, now),
        *await _alerts(session, now),
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
