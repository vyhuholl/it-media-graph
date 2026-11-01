"""Reading the Telegram seeds' YouTube links, as of now.

Reads, and writes nothing: which families link which YouTube channel is
joined from Telegram's posts and the API's answers, and the API's half
lives only in ``yt_raw_*`` for 30 days — so the join is made each time it
is asked for and never stored.

One transaction at ``REPEATABLE READ``: the inventory, the stored
payloads, the lookups, the families and the posts are read as of one
moment, so a lookup run landing between two of the queries cannot make
a name both answered and awaiting.

The posts are streamed through a SQL filter, ``PREFILTER``, that is a
superset of what the reader in ``derive/tg_youtube.py`` can find
anything in: a seed's history is hundreds of thousands of posts, and
only a few per cent mention YouTube at all. The totals of what was
scanned are counted over every own post regardless.
"""

import logging
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any

from sqlalchemy import ColumnElement, Text, cast, func, select
from sqlalchemy.ext.asyncio import AsyncSession

from itgraph.db.affiliation import family_keys, family_of
from itgraph.db.models import Channel, ChannelStatus, RawMessage
from itgraph.db.yt_lookups import lookups_in_date
from itgraph.db.yt_references import read_known
from itgraph.derive.tg_youtube import (
    PREFILTER,
    Known,
    Name,
    NameKind,
    Post,
    Reading,
    Scanned,
    name_channels,
    read_post,
)

__all__ = [
    "TELEGRAM_LINK_SOURCE_STATUSES",
    "TelegramLinks",
    "read_telegram_links",
]

logger = logging.getLogger(__name__)

# Whose posts are read. Listed rather than "not rejected", as
# `derive/edges.DERIVATION_SOURCE_STATUSES` is: a status added later is
# admitted on purpose or not at all. Seeds only — narrower than the
# forward graph's on purpose: evidence about YouTube comes from the
# reviewed crowd.
TELEGRAM_LINK_SOURCE_STATUSES = (ChannelStatus.SEED,)

# Rows fetched per round trip while streaming the posts.
_BATCH = 500


@dataclass(frozen=True, slots=True)
class TelegramLinks:
    """The reading, and what it was named against."""

    reading: Reading
    known: Known


def _own_seed_posts() -> list[ColumnElement[bool]]:
    """Posts of seeds that are not chats, written by the channel itself.

    The forward header is a JSON ``null`` on a post that is not one, not
    an absent key — Telethon writes every field — so both are own.
    """
    return [
        Channel.status.in_(TELEGRAM_LINK_SOURCE_STATUSES),
        Channel.is_chat.is_(False),
        func.coalesce(
            func.jsonb_typeof(RawMessage.payload["fwd_from"]), "null"
        )
        == "null",
    ]


async def _known(session: AsyncSession, now: datetime) -> Known:
    """The inventory, stored payloads' handles, stored videos, lookups."""
    stored = await read_known(session)
    lookups = await lookups_in_date(session, now=now)
    return Known(
        inventory=stored.channels,
        handles=stored.handles,
        videos=stored.videos,
        lookups={
            Name(NameKind(kind.value), value): channel
            for (kind, value), channel in lookups.items()
        },
    )


async def _scanned(session: AsyncSession, families: dict[int, int]) -> Scanned:
    """How many own seed posts there are, from how many seeds and families."""
    rows = await session.execute(
        select(RawMessage.channel_id, func.count())
        .join(Channel, Channel.tg_id == RawMessage.channel_id)
        .where(*_own_seed_posts())
        .group_by(RawMessage.channel_id)
    )
    per_seed = dict(rows.tuples().all())
    return Scanned(
        posts=sum(per_seed.values()),
        seeds=len(per_seed),
        families=len({family_of(families, seed) for seed in per_seed}),
    )


def _posted_at(raw: Any) -> datetime | None:
    if not isinstance(raw, str):
        return None
    moment = datetime.fromisoformat(raw)
    return moment if moment.tzinfo else moment.replace(tzinfo=UTC)


async def read_telegram_links(
    session: AsyncSession, *, now: datetime | None = None
) -> TelegramLinks:
    """Every seed's own YouTube links, named against what is stored now.

    Must be the first thing done in the session's transaction: the
    isolation level is set on it.
    """
    await session.connection(
        execution_options={"isolation_level": "REPEATABLE READ"}
    )
    moment = now or datetime.now(UTC)
    known = await _known(session, moment)
    families = await family_keys(session)
    scanned = await _scanned(session, families)

    payload = RawMessage.payload
    text = func.concat_ws(
        " ",
        payload["message"].astext,
        cast(payload["entities"], Text),
        payload[("media", "webpage", "url")].astext,
    )
    rows = await session.stream(
        select(
            RawMessage.channel_id,
            RawMessage.msg_id,
            payload["message"].astext,
            payload["entities"],
            payload[("media", "webpage", "url")].astext,
            payload["date"].astext,
        )
        .join(Channel, Channel.tg_id == RawMessage.channel_id)
        .where(*_own_seed_posts(), text.op("~*")(PREFILTER))
        .order_by(RawMessage.channel_id, RawMessage.msg_id)
        .execution_options(yield_per=_BATCH)
    )
    posts: list[Post] = []
    async for channel, msg_id, message, entities, preview, date in rows:
        posted_at = _posted_at(date)
        if posted_at is None:
            logger.debug("post %s of %s has no date; skipped", msg_id, channel)
            continue
        links = read_post(
            {
                "message": message,
                "entities": entities,
                "media": {"webpage": {"url": preview}} if preview else None,
            }
        )
        posts.append(
            Post(
                channel=channel,
                family=family_of(families, channel),
                posted_at=posted_at,
                links=links,
            )
        )

    reading = name_channels(posts, known, scanned=scanned)
    logger.info(
        "read %d own posts of %d seeds: %d link YouTube, %d channels named, "
        "%d names awaiting a lookup",
        scanned.posts,
        scanned.seeds,
        reading.linking_posts,
        len(reading.channels),
        len(reading.awaiting),
    )
    return TelegramLinks(reading=reading, known=known)
