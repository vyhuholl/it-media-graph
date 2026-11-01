"""The search pass: the operator's queries, each asked at most once a month.

For each query, in the order given:

- a run of it, of the same type, in date — made in the last 30 days —
  means no request, unless ``force``;
- a video search asks for uploads of the last 30 days, or, forced over a
  run in date, only for those since that run;
- up to ``pages`` pages, following the next-page token, each page's
  results committed before the next request. The run itself is written
  with its first page, so a request that failed leaves nothing that
  would make the query look answered.

Spends the search pool alone. A spent pool, ``quotaExceeded`` from the
search bucket, a failure or ``--limit`` ends the run with what it
stored. Logs run ids and counts — never a query, a request URL or a
result: ``search.list`` URLs carry the query, so httpx's line for each
request is kept out for the run.
"""

import logging
from collections.abc import Sequence
from dataclasses import dataclass
from datetime import UTC, datetime

from itgraph.db.models import YtSearchType
from itgraph.db.session import Database
from itgraph.db.yt_quota import QuotaError
from itgraph.db.yt_search import add_page, latest_run_in_date, start_run
from itgraph.yt import search
from itgraph.yt.client import YouTube, request_urls_unlogged
from itgraph.yt.errors import RequestLimitReached, YtError
from itgraph.yt_policy import RETENTION

__all__ = ["SearchSummary", "run_searches"]

logger = logging.getLogger(__name__)


@dataclass(slots=True)
class SearchSummary:
    """What a search run asked, skipped and found, and why it stopped."""

    asked: int = 0
    skipped: int = 0
    results: int = 0
    requests: int = 0
    limited: RequestLimitReached | None = None
    stopped: QuotaError | YtError | None = None

    def line(self) -> str:
        return (
            f"queries asked {self.asked}, skipped {self.skipped} (asked in "
            f"the last 30 days; --force asks again); results {self.results}; "
            f"requests {self.requests}"
        )


async def run_searches(
    youtube: YouTube,
    database: Database,
    queries: Sequence[str],
    kind: YtSearchType,
    *,
    pages: int = 1,
    force: bool = False,
) -> SearchSummary:
    """Search every query that is due. Never raises for YouTube."""
    summary = SearchSummary()
    try:
        with request_urls_unlogged():
            for query in queries:
                await _search_one(
                    youtube, database, query, kind, pages, force, summary
                )
    except RequestLimitReached as exc:
        summary.limited = exc
    except (QuotaError, YtError) as exc:
        summary.stopped = exc
    summary.requests = youtube.requests_made
    logger.info(
        "searched %d queries, skipped %d: %d results in %d requests",
        summary.asked,
        summary.skipped,
        summary.results,
        summary.requests,
    )
    return summary


async def _search_one(
    youtube: YouTube,
    database: Database,
    query: str,
    kind: YtSearchType,
    pages: int,
    force: bool,
    summary: SearchSummary,
) -> None:
    async with database.session() as session:
        previous = await latest_run_in_date(session, query, kind)
    if previous is not None and not force:
        summary.skipped += 1
        return

    asked_at = datetime.now(UTC)
    published_after = None
    if kind is YtSearchType.VIDEO:
        published_after = (
            previous if previous is not None else asked_at - RETENTION
        )

    run_id: int | None = None
    position = 1
    token: str | None = None
    for _ in range(pages):
        found = await search.page(
            youtube, query, kind, published_after=published_after, token=token
        )
        async with database.session() as session:
            if run_id is None:
                run_id = await start_run(
                    session,
                    query,
                    kind,
                    found.channels,
                    asked_at=asked_at,
                    published_after=published_after,
                )
            else:
                await add_page(session, run_id, position, found.channels)
        position += len(found.channels)
        summary.results += len(found.channels)
        logger.debug(
            "search run %d: %d results on this page",
            run_id,
            len(found.channels),
        )
        if found.next_token is None:
            break
        token = found.next_token
    summary.asked += 1
