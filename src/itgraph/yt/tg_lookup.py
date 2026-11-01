"""The lookup pass: every name the Telegram seeds link that nothing answers.

Reads the seeds' links as they stand (``db/yt_telegram_links.py``),
takes the names that no stored payload and no lookup in date answers,
and asks YouTube about them, best evidenced first — most distinct seed
families linking a name — so that a run bounded by ``--limit`` spends
on what matters most. Videos go fifty to a request, taken in that same
order; handles and legacy names one each.

What it spends, and what it does not:

- the general pool, with no floor: the history walk's floor exists to
  leave this kind of command room;
- every answer, "not found" included, is committed before the next
  request, so a run stopped by its limit, a spent pool or a failure
  keeps what it learned, and the next run does not pay for it again;
- no Telegram session and no Telegram request: the posts are already in
  ``raw_messages``.

Logs ids and counts only — never a handle, a legacy name or a link:
httpx's line for each request, whose URL carries the name asked about,
is kept out for the run (``request_urls_unlogged``).
"""

import logging
from collections import Counter
from collections.abc import Mapping
from dataclasses import dataclass, field
from datetime import UTC, datetime

from itgraph.db.models import YtLookupKind
from itgraph.db.session import Database
from itgraph.db.yt_lookups import store_lookups
from itgraph.db.yt_quota import QuotaError
from itgraph.db.yt_telegram_links import read_telegram_links
from itgraph.derive.tg_youtube import Name, NameKind, to_look_up
from itgraph.yt.client import YouTube, request_urls_unlogged
from itgraph.yt.errors import RequestLimitReached, YtError
from itgraph.yt.lookups import (
    VIDEOS_PER_CALL,
    channel_of_handle,
    channel_of_legacy_name,
    channels_of_videos,
)

__all__ = ["LookupSummary", "run_lookups"]

logger = logging.getLogger(__name__)


@dataclass(slots=True)
class LookupSummary:
    """What a lookup run asked, what came back, and what is left.

    ``awaiting`` is how many names still await a lookup after the run:
    zero once every linked name has an answer.
    """

    asked: Counter[NameKind] = field(default_factory=Counter)
    found: Counter[NameKind] = field(default_factory=Counter)
    not_found: Counter[NameKind] = field(default_factory=Counter)
    requests: int = 0
    awaiting: int = 0
    limited: RequestLimitReached | None = None
    stopped: QuotaError | YtError | None = None

    def lines(self) -> list[str]:
        if not sum(self.asked.values()) and not self.awaiting:
            return ["nothing awaits a lookup"]
        lines = [
            f"{kind.value + 's':<14} looked up {self.asked[kind]:>5}, "
            f"found {self.found[kind]:>5}, not found {self.not_found[kind]:>5}"
            for kind in NameKind
        ]
        lines.append(f"requests {self.requests}")
        lines.append(f"still awaiting a lookup: {self.awaiting}")
        return lines


async def _keep(
    database: Database, answers: Mapping[Name, str | None]
) -> None:
    """Commit one request's answers before the next request is made."""
    async with database.session() as session:
        await store_lookups(
            session,
            {
                (YtLookupKind(name.kind.value), name.value): channel
                for name, channel in answers.items()
            },
            fetched_at=datetime.now(UTC),
        )


async def run_lookups(youtube: YouTube, database: Database) -> LookupSummary:
    """Look up every name awaiting one. Never raises for YouTube.

    A limit, a spent quota or a failure ends the run with what it did,
    already committed, and is returned in the summary.
    """
    async with database.session() as session:
        links = await read_telegram_links(session)
    names = to_look_up(links.reading)
    # The videos in the same order, so a batch is always the best
    # evidenced fifty still waiting — a suffix of this list.
    videos = [name for name in names if name.kind is NameKind.VIDEO]
    answered: set[Name] = set()
    summary = LookupSummary()

    try:
        with request_urls_unlogged():
            await _ask(youtube, database, names, videos, answered, summary)
    except RequestLimitReached as exc:
        summary.limited = exc
    except (QuotaError, YtError) as exc:
        summary.stopped = exc
    summary.requests = youtube.requests_made
    summary.awaiting = len(names) - len(answered)
    logger.info(
        "looked up %d names in %d requests: %d found, %d not found; "
        "%d still awaiting",
        sum(summary.asked.values()),
        summary.requests,
        sum(summary.found.values()),
        sum(summary.not_found.values()),
        summary.awaiting,
    )
    return summary


async def _ask(
    youtube: YouTube,
    database: Database,
    names: list[Name],
    videos: list[Name],
    answered: set[Name],
    summary: LookupSummary,
) -> None:
    """Ask about each name in order, committing each request's answers.

    ``answered`` and ``summary`` are filled as it goes, so whatever stops
    it leaves them true to what was stored.
    """
    next_video = 0
    for name in names:
        if name in answered:
            continue
        if name.kind is NameKind.VIDEO:
            batch = videos[next_video : next_video + VIDEOS_PER_CALL]
            next_video += len(batch)
            found = await channels_of_videos(
                youtube, [video.value for video in batch]
            )
            answers = {
                Name(NameKind.VIDEO, video): channel
                for video, channel in found.items()
            }
        elif name.kind is NameKind.HANDLE:
            answers = {name: await channel_of_handle(youtube, name.value)}
        else:
            answers = {name: await channel_of_legacy_name(youtube, name.value)}
        await _keep(database, answers)
        answered.update(answers)
        for asked, channel in answers.items():
            summary.asked[asked.kind] += 1
            if channel is None:
                summary.not_found[asked.kind] += 1
            else:
                summary.found[asked.kind] += 1
