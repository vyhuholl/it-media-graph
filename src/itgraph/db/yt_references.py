"""Reading the mention graph out of the stored payloads, as of now.

The sources are seeds' videos — title and description — and seeds'
featured lists in date, each read as a reference of the seed itself.

Reads, and writes nothing: no edge, no pending target, no count is
stored anywhere; a handle read out of a description lives only in its
payload. Whatever a refresh or a removal did to the payloads, the next
reading already reflects.

One transaction at ``REPEATABLE READ``: the known handles, the inventory,
the stored videos and the sources are read as of one moment, so a
refresh landing between two of the queries cannot make a handle both
known and pending.
"""

import logging

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from itgraph.db.models import (
    ChannelStatus,
    YtChannel,
    YtRawChannel,
    YtRawVideo,
)
from itgraph.db.yt_lookups import lookups_in_date
from itgraph.db.yt_sections import featured_lists
from itgraph.derive.yt_featured import featured_ids
from itgraph.derive.yt_mention_candidates import Naming, name_channels
from itgraph.derive.yt_references import (
    Known,
    References,
    Source,
    normalize_handle,
    read_text,
    resolve,
)
from itgraph.derive.yt_videos import moment_of

__all__ = [
    "YT_REFERENCE_SOURCE_STATUSES",
    "read_known",
    "read_mention_queue",
    "read_references",
]

logger = logging.getLogger(__name__)

# Whose videos are read as sources. Listed rather than "not rejected",
# as `derive/edges.DERIVATION_SOURCE_STATUSES` is: a status added later
# is admitted on purpose or not at all. Seeds only — the walk and the
# poll loop read nobody else, and a reference from an unreviewed channel
# is not evidence about the crowd.
YT_REFERENCE_SOURCE_STATUSES = (ChannelStatus.SEED,)

# Rows fetched per round trip while streaming the sources.
_BATCH = 500


async def read_known(session: AsyncSession) -> Known:
    """What the inventory and the stored videos answer without a request.

    Every stored channel payload's handle, whatever the channel's status
    — the refresh keeps them all — read out of ``customUrl`` only when it
    is an ``@handle``. Every channel record's id. Every stored video's
    channel.
    """
    handles: dict[str, set[str]] = {}
    custom_url = YtRawChannel.payload["snippet"]["customUrl"].astext
    rows = await session.execute(select(YtRawChannel.channel_id, custom_url))
    for channel, url in rows.tuples():
        if url is None or not url.startswith("@"):
            continue
        if (handle := normalize_handle(url)) is not None:
            handles.setdefault(handle, set()).add(channel)
    channels = frozenset(await session.scalars(select(YtChannel.channel_id)))
    videos = await session.execute(
        select(YtRawVideo.video_id, YtRawVideo.channel_id)
    )
    return Known(
        handles={handle: frozenset(ids) for handle, ids in handles.items()},
        channels=channels,
        videos=dict(videos.tuples().all()),
    )


async def read_references(session: AsyncSession) -> References:
    """Every reference the seeds' videos make, as the payloads stand now.

    Must be the first thing done in the session's transaction: the
    isolation level is set on it.
    """
    await session.connection(
        execution_options={"isolation_level": "REPEATABLE READ"}
    )
    known = await read_known(session)

    snippet = YtRawVideo.payload["snippet"]
    rows = await session.stream(
        select(
            YtRawVideo.video_id,
            YtRawVideo.channel_id,
            snippet["title"].astext,
            snippet["description"].astext,
            snippet["publishedAt"].astext,
        )
        .join(YtChannel, YtChannel.channel_id == YtRawVideo.channel_id)
        .where(YtChannel.status.in_(YT_REFERENCE_SOURCE_STATUSES))
        .order_by(YtRawVideo.channel_id, YtRawVideo.video_id)
        .execution_options(yield_per=_BATCH)
    )
    sources: list[Source] = []
    async for video_id, channel, title, description, published in rows:
        named = read_text(f"{title or ''}\n{description or ''}")
        logger.debug(
            "video %s of %s: %d channel links, %d video links, %d mentions",
            video_id,
            channel,
            len(named.channel_ids)
            + len(named.handles)
            + len(named.legacy_names),
            len(named.video_ids),
            len(named.mentions),
        )
        sources.append(
            Source(
                video_id=video_id,
                channel_id=channel,
                published_at=moment_of(published),
                named=named,
            )
        )

    featured = {
        seed: featured_ids(payload)
        for seed, payload in (await featured_lists(session)).items()
    }
    references = resolve(sources, known, featured)
    logger.info(
        "read %d videos and %d featured lists of %d seeds: %d edges, "
        "%d pending targets",
        references.videos,
        references.featured_lists,
        references.seeds,
        len(references.edges),
        len(references.pending),
    )
    return references


async def read_mention_queue(
    session: AsyncSession,
) -> tuple[References, Naming]:
    """The mention graph, and the channels its references name, as of now.

    The graph and the lookups in date are read in the one transaction
    ``read_references`` opens, so a lookup committed in between cannot
    make a reference both named and awaiting. Must be the first thing
    done in the session.
    """
    references = await read_references(session)
    lookups = await lookups_in_date(session)
    naming = name_channels(
        references,
        {
            (kind.value, value): channel
            for (kind, value), channel in lookups.items()
        },
    )
    logger.info(
        "the seeds' videos name %d channels; %d references await a lookup, "
        "%d named none, %d named their own channel",
        len(naming.channels),
        len(naming.awaiting),
        naming.not_found,
        naming.own,
    )
    return references, naming
