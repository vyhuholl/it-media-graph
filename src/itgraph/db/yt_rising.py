"""The rows the YouTube rising rankings read, in one snapshot.

Reads, and writes nothing: which families referred to which channel is
joined from the stored payloads, the lookups, the featured readings
and the charts each time it is asked, and never stored — the ranking's
own rule, and Telegram's before it.

One transaction at ``REPEATABLE READ``, as the queues' loaders open
it: the inventory, the families, both platforms' evidence, the
readings and the day's charts are read as of one moment, so a lookup
run landing between two of the queries cannot make a name both
answered and awaiting. The loader is the one place the level is set;
the streams and readers it reads through set none of their own.

The mention side re-parses the seeds' stored descriptions, as the
mention queue does — seconds of work at a few hundred seeds, paid by
whoever asks. A featured list names no channel here: a standing
feature is not this ranking's evidence, only an appearance is, and
appearances are read from the two newest readings below.
"""

import logging
from collections.abc import Mapping
from datetime import UTC, datetime, time
from typing import Any
from zoneinfo import ZoneInfo

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from itgraph.config import settings
from itgraph.db import affiliation, yt_affiliation
from itgraph.db.models import (
    ChannelStatus,
    YtChannel,
    YtRawChannelSections,
)
from itgraph.db.yt_chart import charts_of_the_day
from itgraph.db.yt_references import video_sources
from itgraph.db.yt_sections import seeds_without_list
from itgraph.db.yt_telegram_links import known_answers, own_seed_posts
from itgraph.derive.tg_youtube import (
    Known,
    Name,
    PostLinks,
    name_links,
    name_post,
)
from itgraph.derive.yt_featured import featured_ids
from itgraph.derive.yt_rising import (
    FeaturedReadings,
    LinkReference,
    YtRisingRows,
)

__all__ = ["chart_pairs", "load_yt_rising_rows", "moscow_day_start"]

logger = logging.getLogger(__name__)

# The chart's day: the reading runs at five in the morning Moscow, and
# "today's chart" is today's there. A constant of the module, as the
# quota day's Los Angeles is of the ledger's.
MOSCOW = ZoneInfo("Europe/Moscow")


def moscow_day_start(moment: datetime) -> datetime:
    """The start of the Moscow day ``moment`` falls in, as UTC."""
    local = moment.astimezone(MOSCOW)
    return datetime.combine(local.date(), time(), tzinfo=MOSCOW).astimezone(
        UTC
    )


def chart_pairs(payload: Mapping[str, Any]) -> list[tuple[str, str]]:
    """A chart reading's payload as (video id, channel id) pairs, in
    the reading's order."""
    pairs: list[tuple[str, str]] = []
    items = payload.get("items")
    for item in items if isinstance(items, list) else ():
        if not isinstance(item, dict):
            continue
        video = item.get("id")
        snippet = item.get("snippet")
        channel = (
            snippet.get("channelId") if isinstance(snippet, dict) else None
        )
        if isinstance(video, str) and isinstance(channel, str):
            pairs.append((video, channel))
    return pairs


async def _telegram_evidence(
    session: AsyncSession, known: Known, moment: datetime
) -> tuple[list[LinkReference], set[Name], set[Name]]:
    """Every Telegram seed's own links that name a channel, dated by
    the post, and the names that could not be named."""
    tg_families = await affiliation.family_keys(session)
    links: list[LinkReference] = []
    awaiting: set[Name] = set()
    not_found: set[Name] = set()
    async for post in own_seed_posts(session, tg_families):
        named = name_post(post, known)
        # The stream has already applied the family rule: a solo channel
        # is its own family.
        family = f"tg:{post.family}"
        for channel in named.named:
            links.append(
                LinkReference(
                    channel=channel, family=family, at=post.posted_at
                )
            )
        awaiting.update(named.awaiting)
        not_found.update(named.not_found)
    return links, awaiting, not_found


async def _video_evidence(
    session: AsyncSession, known: Known
) -> tuple[list[LinkReference], set[Name], set[Name]]:
    """Every YouTube seed's video reference that names a channel, dated
    by the video's publication, and the names that could not be named.

    Named by the same rule the Telegram links are — a channel id is its
    own answer, a handle or a video goes through the stored payloads and
    the lookups in date — which is what the mention queue resolves them
    by, minus the featured lists: a standing feature is not this
    ranking's evidence. A reference to the seed's own channel is
    dropped, as the queue drops it.
    """
    yt_families = await yt_affiliation.family_keys(session)
    references: list[LinkReference] = []
    awaiting: set[Name] = set()
    not_found: set[Name] = set()
    async for source in video_sources(session):
        if source.published_at is None:
            continue  # undated evidence cannot be placed in a window
        named = name_links(
            PostLinks(
                channel_ids=frozenset(source.named.channel_ids),
                handles=frozenset(source.named.handles),
                legacy_names=frozenset(source.named.legacy_names),
                video_ids=frozenset(source.named.video_ids),
            ),
            known,
        )
        family = (
            f"yt:{yt_affiliation.family_of(yt_families, source.channel_id)}"
        )
        for channel in named.named:
            if channel == source.channel_id:
                continue
            references.append(
                LinkReference(
                    channel=channel, family=family, at=source.published_at
                )
            )
        awaiting.update(named.awaiting)
        not_found.update(named.not_found)
    return references, awaiting, not_found


async def _two_newest_readings(
    session: AsyncSession, yt_families: Mapping[str, str]
) -> list[FeaturedReadings]:
    """Every seed not gone's two newest featured readings, with the
    seed's family — the previous one of any age, so an appearance is
    read from the pair."""
    ranked = select(
        YtRawChannelSections.channel_id.label("channel_id"),
        YtRawChannelSections.payload.label("payload"),
        YtRawChannelSections.fetched_at.label("fetched_at"),
        func.row_number()
        .over(
            partition_by=YtRawChannelSections.channel_id,
            order_by=YtRawChannelSections.fetched_at.desc(),
        )
        .label("rank"),
    ).subquery()
    rows = await session.execute(
        select(ranked.c.channel_id, ranked.c.payload, ranked.c.fetched_at)
        .join(YtChannel, YtChannel.channel_id == ranked.c.channel_id)
        .where(
            YtChannel.status == ChannelStatus.SEED,
            YtChannel.gone_at.is_(None),
            ranked.c.rank <= 2,
        )
        .order_by(ranked.c.channel_id, ranked.c.fetched_at)
    )
    listed: dict[str, list[tuple[list[str], datetime]]] = {}
    for channel, payload, fetched_at in rows.tuples():
        listed.setdefault(channel, []).append(
            (featured_ids(payload), fetched_at)
        )
    return [
        FeaturedReadings(
            seed=channel,
            family=f"yt:{yt_affiliation.family_of(yt_families, channel)}",
            newest_at=entries[-1][1],
            newest=tuple(entries[-1][0]),
            previous=tuple(entries[0][0]) if len(entries) > 1 else None,
        )
        for channel, entries in listed.items()
    ]


async def load_yt_rising_rows(
    session: AsyncSession, *, now: datetime | None = None
) -> YtRisingRows:
    """Everything both rankings read, as of one snapshot.

    Must be the first thing done in the session's transaction: the
    isolation level is set on it.
    """
    await session.connection(
        execution_options={"isolation_level": "REPEATABLE READ"}
    )
    moment = now or datetime.now(UTC)

    inventory = frozenset(await session.scalars(select(YtChannel.channel_id)))
    yt_families = await yt_affiliation.family_keys(session)
    # The one set of answers both platforms' names resolve through: the
    # lookups are shared, so a name either source asked about costs
    # nothing to the other.
    known = await known_answers(session, moment)

    links, tg_awaiting, tg_not_found = await _telegram_evidence(
        session, known, moment
    )
    references, mention_awaiting, mention_not_found = await _video_evidence(
        session, known
    )
    featured = await _two_newest_readings(session, yt_families)
    out_of_date = await seeds_without_list(session, now=moment)
    charts = {
        region: chart_pairs(payload)
        for region, payload in (
            await charts_of_the_day(
                session,
                settings.youtube_chart_regions,
                day_start=moscow_day_start(moment),
            )
        ).items()
    }

    logger.debug(
        "yt rising rows: %d links, %d references, %d featured seeds, "
        "%d regions charted",
        len(links),
        len(references),
        len(featured),
        len(charts),
    )
    return YtRisingRows(
        inventory=inventory,
        links=tuple(links),
        references=tuple(references),
        featured=tuple(featured),
        chart_regions=tuple(settings.youtube_chart_regions),
        charts=charts,
        awaiting_lookup=len(tg_awaiting | mention_awaiting),
        not_found=len(tg_not_found | mention_not_found),
        lists_out_of_date=out_of_date,
    )
