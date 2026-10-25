"""The import pass: operator entries → channel records, through the API.

Each entry is resolved to a channel id before a record exists, because a
list assembled by a language model carries renamed, dead and invented
handles, and an id is the only thing that says which channel a handle
meant today. What does not resolve creates nothing.

What it spends, and what it does not:

- an entry the inventory already answers — an id it holds, a handle a
  stored payload carries — costs no request;
- ids go fifty to a call, handles and legacy names one each;
- every call's results are committed before the next is made, so a run
  stopped by ``--limit``, a spent quota or a failure keeps what it did,
  and the same input continues it.

Records are created unreviewed. No review is applied here, ever: a
handle that now belongs to someone else is found by a person reading
what it resolved to, not by the list that named it.
"""

from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from datetime import UTC, datetime
from itertools import batched

from itgraph.db.session import Database
from itgraph.db.yt_channels import (
    create_channels,
    ids_by_handle,
    known_ids,
    store_payloads,
)
from itgraph.db.yt_quota import QuotaError
from itgraph.yt.channels import (
    IDS_PER_CALL,
    Resource,
    by_handle,
    by_ids,
    by_username,
)
from itgraph.yt.client import YouTube
from itgraph.yt.errors import RequestLimitReached, YtError
from itgraph.yt_entries import (
    CHANNEL_ID,
    ChannelIdLookup,
    Entry,
    HandleLookup,
    LegacyNameLookup,
)

__all__ = ["AddSummary", "add_channels"]


@dataclass(slots=True)
class AddSummary:
    """What an import did with each entry, in input order.

    ``refreshed`` resolved, through a request, to a channel the inventory
    already held — its review is untouched and its data now current.
    ``known`` cost nothing. ``unreached`` were never asked about because
    the run stopped first; the same input picks them up.
    """

    added: list[tuple[Entry, Resource]] = field(default_factory=list)
    refreshed: list[Entry] = field(default_factory=list)
    known: list[Entry] = field(default_factory=list)
    same: list[tuple[Entry, Entry]] = field(default_factory=list)
    not_found: list[Entry] = field(default_factory=list)
    unreached: list[Entry] = field(default_factory=list)
    requests: int = 0
    limited: RequestLimitReached | None = None
    stopped: QuotaError | YtError | None = None

    def line(self) -> str:
        return (
            f"added {len(self.added)}, "
            f"already known {len(self.known) + len(self.refreshed)}, "
            f"same channel {len(self.same)}, "
            f"not found {len(self.not_found)}, "
            f"requests {self.requests}"
        )

    def failures(self) -> list[tuple[Entry, str]]:
        """Entries to try again, each with the reason it failed."""
        return [
            (entry, "YouTube returns no channel for it")
            for entry in self.not_found
        ]


class _UnacceptableId(YtError):
    """YouTube returned an id the inventory's check would refuse."""

    def __init__(self, channel_id: str) -> None:
        super().__init__(
            f"YouTube returned a channel id of a shape the inventory does "
            f"not accept ({channel_id!r}). The check in `yt_channels` "
            "assumes UC and 22 characters, which YouTube never documented; "
            "relax it by migration rather than dropping the channel"
        )


async def _keep(database: Database, found: Mapping[str, Resource]) -> set[str]:
    """Record what resolved and store what YouTube said. Returns new ids."""
    for channel_id in found:
        if not CHANNEL_ID.match(channel_id):
            raise _UnacceptableId(channel_id)
    async with database.session() as session:
        created = await create_channels(session, found)
        await store_payloads(session, found, fetched_at=datetime.now(UTC))
    return set(created)


async def add_channels(
    youtube: YouTube, database: Database, entries: Sequence[Entry]
) -> AddSummary:
    """Resolve and record every entry. Never raises for YouTube.

    A limit, a spent quota or a failure ends the run with what it did,
    already committed, and is returned in the summary.
    """
    summary = AddSummary()

    async with database.session() as session:
        held = await known_ids(
            session,
            [
                entry.lookup.channel_id
                for entry in entries
                if isinstance(entry.lookup, ChannelIdLookup)
            ],
        )
        stored = await ids_by_handle(
            session,
            [
                entry.lookup.handle
                for entry in entries
                if isinstance(entry.lookup, HandleLookup)
            ],
        )

    resolved: dict[Entry, str | None] = {}
    asked: set[Entry] = set()
    created: set[str] = set()
    fetched: dict[str, Resource] = {}
    by_id: list[Entry] = []
    one_by_one: list[Entry] = []
    for entry in entries:
        match entry.lookup:
            case ChannelIdLookup(channel_id) if channel_id in held:
                resolved[entry] = channel_id
            case HandleLookup(handle) if handle in stored:
                resolved[entry] = stored[handle]
            case ChannelIdLookup():
                by_id.append(entry)
            case _:
                one_by_one.append(entry)

    try:
        for batch in batched(by_id, IDS_PER_CALL):
            ids = [
                entry.lookup.channel_id
                for entry in batch
                if isinstance(entry.lookup, ChannelIdLookup)
            ]
            found = await by_ids(youtube, ids)
            created |= await _keep(database, found)
            fetched |= found
            for entry, channel_id in zip(batch, ids, strict=True):
                resolved[entry] = channel_id if channel_id in found else None
                asked.add(entry)

        for entry in one_by_one:
            match entry.lookup:
                case HandleLookup(handle):
                    resource = await by_handle(youtube, handle)
                case LegacyNameLookup(name):
                    resource = await by_username(youtube, name)
                case ChannelIdLookup():  # pragma: no cover - sorted above
                    continue
            if resource is not None:
                created |= await _keep(database, {resource["id"]: resource})
                fetched[resource["id"]] = resource
            resolved[entry] = None if resource is None else resource["id"]
            asked.add(entry)
    except RequestLimitReached as exc:
        summary.limited = exc
    except (QuotaError, YtError) as exc:
        summary.stopped = exc
    summary.requests = youtube.requests_made

    first: dict[str, Entry] = {}
    for entry in entries:
        if entry not in resolved:
            summary.unreached.append(entry)
            continue
        resolved_id = resolved[entry]
        if resolved_id is None:
            summary.not_found.append(entry)
        elif resolved_id in first:
            summary.same.append((entry, first[resolved_id]))
        else:
            first[resolved_id] = entry
            if resolved_id in created:
                summary.added.append((entry, fetched[resolved_id]))
            elif entry in asked:
                summary.refreshed.append(entry)
            else:
                summary.known.append(entry)
    return summary
