"""The detection pass: load, detect, store, report.

The one place the pure cascade measurement meets a database. It reads the
derived edges and the confirmed families, runs the measurement in memory,
and writes alerts back. It issues no Telegram request, takes no session
lease, reads no metric snapshots, and modifies nothing it read — which is
what makes it safe to put on a short schedule beside a running collector.

Its evidence is only as fresh as ``itgraph derive``, and the summary says
so. That matters more here than in any other pass in this project,
because an alerting system's healthy state is silence: without the
freshness line, "nothing has travelled today" and "nothing has been
derived for a week" are the same observation.
"""

import logging
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta

from sqlalchemy import BigInteger, func, select
from sqlalchemy.ext.asyncio import AsyncSession

from itgraph.alerts.cascade import RepostEdge, crossings
from itgraph.config import settings
from itgraph.db.affiliation import family_keys, family_of
from itgraph.db.alerts import raise_cascades, raised_bands
from itgraph.db.models import (
    AlertKind,
    Channel,
    ChannelStatus,
    Edge,
    EdgeKind,
    RawMessage,
)
from itgraph.db.session import Database

__all__ = ["CascadeSummary", "run_cascades"]

logger = logging.getLogger(__name__)

# The referenced post's album, out of the raw layer — and the asymmetry
# worth stopping at. `edges.grouped_id` describes the *referencing*
# message, the repost, and says nothing about the album being referenced.
# The post being alerted about is the destination, so its grouping is
# read from `raw_messages` joined on the destination key.
# `notebooks/cited_posts.py` deliberately does not collapse albums for
# exactly this reason, and confusing the two columns is the easiest
# mistake available in this file.
ALBUM = RawMessage.payload["grouped_id"].astext.cast(BigInteger)


@dataclass(frozen=True, slots=True)
class CascadeSummary:
    """What a pass found, and what it could see.

    Three different facts, kept apart because conflating two of them is
    the mistake this class was written with the first time.

    ``newest_repost_at`` is **activity**: when the most recent repost in
    the window was published. Hours old is entirely normal — it means
    nobody has reposted anything lately, which is a fact about the world
    and not a fault.

    ``derived_at`` is **pipeline freshness**: when derivation last
    produced an edge. This is the one worth warning about, because an
    alerting system whose healthy state is silence cannot otherwise
    distinguish "nothing travelled" from "nothing was derived".

    ``collected_at`` is what makes that warning precise. Derivation that
    finds nothing new writes nothing, so ``derived_at`` standing still
    proves nothing on its own — it only means something when collection
    has stored messages *since*, which is exactly the situation where a
    derivation run is owed.
    """

    considered: int
    crossed: int
    raised: int
    newest_repost_at: datetime | None
    derived_at: datetime | None
    collected_at: datetime | None
    now: datetime

    def undelivered_derivation(self) -> timedelta | None:
        """How far collection has run ahead of derivation, if it has.

        ``None`` when derivation has seen everything collected — which
        includes the case where neither has done anything lately, and
        that is the point: an idle night must not read as a broken
        pipeline.
        """
        if self.collected_at is None:
            return None
        if self.derived_at is None:
            return self.now - self.collected_at
        gap = self.collected_at - self.derived_at
        return gap if gap > timedelta(0) else None

    def line(self) -> str:
        line = (
            f"{self.considered} repost(s) in the window, "
            f"{self.crossed} crossing(s), {self.raised} new alert(s)"
        )
        if self.newest_repost_at is None:
            line += "; nothing reposted in the window"
        else:
            line += (
                f"; newest repost {_ago(self.now - self.newest_repost_at)} old"
            )

        behind = self.undelivered_derivation()
        if behind is not None and behind > timedelta(
            hours=settings.alert_stale_edges_hours
        ):
            line += (
                f"; collection is {_ago(behind)} ahead of derivation — "
                "run `itgraph derive`"
            )
        return line


def _ago(age: timedelta) -> str:
    """A duration a person reads without converting anything."""
    minutes = max(int(age.total_seconds()) // 60, 0)
    if minutes < 60:
        return f"{minutes}m"
    if minutes < 60 * 24:
        return f"{minutes // 60}h{minutes % 60:02d}m"
    return f"{minutes // (60 * 24)}d"


async def _album_first_parts(
    session: AsyncSession, *, since: datetime
) -> dict[tuple[int, int], int]:
    """The message id an album's parts collapse to, per part.

    The first part, which is what a ``t.me`` link to an album resolves
    to and the same choice ``anomalous_posts.py`` makes. Keyed on
    ``(channel_id, grouped_id)`` and never on the group id alone, because
    the id is only unique within a channel.

    Only posts inside the window are considered, so this stays a small
    mapping rather than a second copy of the corpus.
    """
    published = RawMessage.payload["date"].astext.cast(
        Edge.dst_published_at.type
    )
    rows = await session.execute(
        select(
            RawMessage.channel_id,
            ALBUM,
            func.min(RawMessage.msg_id),
        )
        .where(ALBUM.is_not(None), published >= since)
        .group_by(RawMessage.channel_id, ALBUM)
    )
    return {
        (channel_id, group): first for channel_id, group, first in rows.all()
    }


async def _pipeline_freshness(
    session: AsyncSession,
) -> tuple[datetime | None, datetime | None]:
    """When derivation last produced an edge, and collection last stored.

    Two whole-table maxima rather than window-bounded ones, deliberately:
    the question is whether the *pipeline* is current, and bounding it by
    the alert window would make a quiet six hours look like a broken
    derivation.
    """
    derived = await session.scalar(select(func.max(Edge.derived_at)))
    collected = await session.scalar(select(func.max(RawMessage.fetched_at)))
    return derived, collected


async def _load(
    session: AsyncSession, *, since: datetime
) -> tuple[list[RepostEdge], datetime | None]:
    """Reposts of recent seed posts, with families and albums resolved.

    Only seed channels are treated as publishers. A post in a channel
    nobody accepted is not something to be told about, and its history is
    not collected either, so there would be nothing to render.
    """
    families = await family_keys(session)
    first_parts = await _album_first_parts(session, since=since)

    statement = (
        select(
            Edge.dst_channel_id,
            Edge.dst_msg_id,
            Edge.dst_published_at,
            Edge.src_channel_id,
            Edge.published_at,
            ALBUM,
        )
        .join(
            RawMessage,
            (RawMessage.channel_id == Edge.dst_channel_id)
            & (RawMessage.msg_id == Edge.dst_msg_id),
        )
        .join(Channel, Channel.tg_id == Edge.dst_channel_id)
        .where(
            Edge.kind == EdgeKind.FORWARD,
            Edge.dst_msg_id.is_not(None),
            Edge.dst_published_at.is_not(None),
            Edge.dst_published_at >= since,
            Channel.status == ChannelStatus.SEED,
        )
    )

    edges: list[RepostEdge] = []
    newest: datetime | None = None
    for row in (await session.execute(statement)).all():
        dst_channel, dst_msg, dst_published, src_channel, posted, album = row
        msg_id = (
            dst_msg
            if album is None
            else first_parts.get((dst_channel, album), dst_msg)
        )
        edges.append(
            RepostEdge(
                post_key=(dst_channel, msg_id),
                post_published_at=dst_published,
                post_family=family_of(families, dst_channel),
                src_family=family_of(families, src_channel),
                reposted_at=posted,
            )
        )
        if newest is None or posted > newest:
            newest = posted

    return edges, newest


async def run_cascades(
    database: Database,
    *,
    bands: tuple[int, ...] | None = None,
    window: timedelta | None = None,
    now: datetime | None = None,
) -> CascadeSummary:
    """Find posts that crossed a band, record them, and report.

    The window bounds what is loaded as well as what is counted, and that
    is what removes the first-run problem structurally: a pass run for
    the first time over a year of edges raises nothing about old posts,
    because no post outside the window can cross a within-window
    threshold. There is nothing to mark as already-handled and no
    backfill guard to remember.
    """
    moment = now or datetime.now(UTC)
    span = window or timedelta(hours=settings.alert_cascade_window_hours)
    tiers = bands or settings.alert_cascade_bands
    since = moment - span

    async with database.session() as session:
        edges, newest = await _load(session, since=since)
        derived, collected = await _pipeline_freshness(session)
        seen = await raised_bands(
            session, kind=AlertKind.REPOST_CASCADE, since=since
        )

    found = crossings(
        edges,
        bands=tiers,
        now=moment,
        window=span,
        already_raised=seen,
    )

    async with database.session() as session:
        written = await raise_cascades(session, found)

    summary = CascadeSummary(
        considered=len(edges),
        crossed=len(found),
        raised=written,
        newest_repost_at=newest,
        derived_at=derived,
        collected_at=collected,
        now=moment,
    )
    logger.info("%s", summary.line())
    return summary
