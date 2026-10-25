"""The YouTube inventory: every read and write of ``yt_channels`` and
``yt_raw_channels``.

Two tables because they hold two kinds of thing. A record is the
operator's — the id, the review, the provenance — and is never deleted.
A payload is YouTube's — title, handle, statistics — and may be kept at
most 30 days (Developer Policies III.E.4): a refresh replaces it, expiry
deletes it, a channel YouTube stops returning loses it. Nothing here
copies a payload field into a record, so deleting a payload is all it
takes to delete the data.
"""

from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass, field, replace
from datetime import UTC, datetime
from typing import Any

from sqlalchemy import ColumnElement, delete, func, or_, select, update
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.ext.asyncio import AsyncSession

from itgraph.db.models import (
    ChannelStatus,
    YtChannel,
    YtChannelKind,
    YtDiscoverySource,
    YtLang,
    YtRawChannel,
    YtRejectReason,
)
from itgraph.yt_entries import CHANNEL_ID as CHANNEL_ID_SHAPE
from itgraph.yt_policy import RETENTION
from itgraph.yt_review import (
    Decision,
    ParsedReview,
    ReviewFileError,
    RowProblem,
)

__all__ = [
    "AmbiguousHandleError",
    "ApplySummary",
    "InventorySummary",
    "Listed",
    "Payload",
    "Review",
    "YtChannelLookupError",
    "YtChannelNotFoundError",
    "apply_review",
    "create_channels",
    "current_review",
    "due_for_refresh",
    "find_channel",
    "ids_by_handle",
    "inventory_summary",
    "known_ids",
    "list_channels",
    "mark_channel",
    "mark_gone",
    "purge_expired",
    "store_payloads",
    "stored_handle",
]

Payload = Mapping[str, object]


async def create_channels(
    session: AsyncSession,
    channel_ids: Iterable[str],
    *,
    discovered_via: YtDiscoverySource = YtDiscoverySource.MANUAL,
) -> list[str]:
    """Create records for channels the inventory does not hold yet.

    Returns the ids actually created. An existing record is left exactly
    as it is — review, provenance, first-seen date: an import must never
    cost the operator a review, and first discovery wins.
    """
    ids = list(dict.fromkeys(channel_ids))
    if not ids:
        return []
    created = await session.scalars(
        insert(YtChannel)
        .values(
            [
                {"channel_id": channel_id, "discovered_via": discovered_via}
                for channel_id in ids
            ]
        )
        .on_conflict_do_nothing(index_elements=[YtChannel.channel_id])
        .returning(YtChannel.channel_id)
    )
    return list(created)


def stored_handle() -> ColumnElement[str]:
    """The handle a stored payload carries: lowercased, without its ``@``.

    Read out of ``customUrl``, which YouTube documents only as "the
    channel's custom URL" and which is assumed to be the ``@handle`` —
    if it is not, a known handle costs one unit on import and ``mark
    @handle`` falls back to the id, and nothing worse happens.
    """
    custom_url = YtRawChannel.payload["snippet"]["customUrl"].astext
    return func.lower(func.ltrim(custom_url, "@"))


async def known_ids(
    session: AsyncSession, channel_ids: Iterable[str]
) -> set[str]:
    """Which of these channel ids the inventory already holds."""
    ids = list(channel_ids)
    if not ids:
        return set()
    rows = await session.scalars(
        select(YtChannel.channel_id).where(YtChannel.channel_id.in_(ids))
    )
    return set(rows)


async def ids_by_handle(
    session: AsyncSession, handles: Iterable[str]
) -> dict[str, str]:
    """Handles a stored payload carries, lowercased, mapped to channel ids.

    Only what is stored now: a payload that expired took its handle with
    it, and such a channel costs one unit to recognise again.
    """
    wanted = [handle.lower() for handle in handles]
    if not wanted:
        return {}
    handle = stored_handle()
    rows = await session.execute(
        select(handle, YtRawChannel.channel_id).where(handle.in_(wanted))
    )
    return {found: channel_id for found, channel_id in rows.tuples()}


async def purge_expired(
    session: AsyncSession, *, now: datetime | None = None
) -> int:
    """Delete every payload fetched more than 30 days ago. Returns how many.

    The records stay, with their reviews; only what YouTube said goes.
    Run by every YouTube command before anything else it does in the
    database, network or not — a payload past its 30 days is a violation
    whether or not anything reads it.
    """
    cutoff = (now or datetime.now(UTC)) - RETENTION
    result = await session.execute(
        delete(YtRawChannel)
        .where(YtRawChannel.fetched_at < cutoff)
        .returning(YtRawChannel.channel_id)
    )
    return len(result.all())


async def store_payloads(
    session: AsyncSession,
    payloads: Mapping[str, Payload],
    *,
    fetched_at: datetime,
) -> list[str]:
    """Store what YouTube just returned, replacing what was there.

    Replaced, never added to: keeping each fetch would be keeping data
    past 30 days by another name. A channel that had been marked gone and
    is returned again is restored. Returns the ids of those restored.
    """
    if not payloads:
        return []
    statement = insert(YtRawChannel).values(
        [
            {
                "channel_id": channel_id,
                "payload": dict(payload),
                "fetched_at": fetched_at,
            }
            for channel_id, payload in payloads.items()
        ]
    )
    await session.execute(
        statement.on_conflict_do_update(
            index_elements=[YtRawChannel.channel_id],
            set_={
                "payload": statement.excluded.payload,
                "fetched_at": statement.excluded.fetched_at,
            },
        )
    )
    restored = await session.scalars(
        update(YtChannel)
        .where(
            YtChannel.channel_id.in_(list(payloads)),
            YtChannel.gone_at.is_not(None),
        )
        .values(gone_at=None)
        .returning(YtChannel.channel_id)
        .execution_options(synchronize_session=False)
    )
    return list(restored)


async def mark_gone(
    session: AsyncSession, channel_ids: Iterable[str], *, now: datetime
) -> list[str]:
    """Record channels YouTube no longer returns, and drop what it said.

    The payload goes at once; the record and its review stay. Returns
    the ids newly marked — one already gone keeps its first date.
    """
    ids = list(channel_ids)
    if not ids:
        return []
    await session.execute(
        delete(YtRawChannel).where(YtRawChannel.channel_id.in_(ids))
    )
    marked = await session.scalars(
        update(YtChannel)
        .where(YtChannel.channel_id.in_(ids), YtChannel.gone_at.is_(None))
        .values(gone_at=now)
        .returning(YtChannel.channel_id)
        .execution_options(synchronize_session=False)
    )
    return list(marked)


async def due_for_refresh(
    session: AsyncSession, *, older_than: datetime
) -> Sequence[str]:
    """Channels whose payload is missing or older than ``older_than``.

    Every status, rejected included: five hundred channels cost ten units
    a cycle, and a channel nobody refreshes is a channel whose title
    quietly expires. Gone channels come back here too, which is how one
    that returns is noticed.
    """
    statement = (
        select(YtChannel.channel_id)
        .outerjoin(
            YtRawChannel, YtRawChannel.channel_id == YtChannel.channel_id
        )
        .where(
            or_(
                YtRawChannel.channel_id.is_(None),
                YtRawChannel.fetched_at < older_than,
            )
        )
        .order_by(YtChannel.first_seen_at, YtChannel.channel_id)
    )
    return (await session.scalars(statement)).all()


# --- review ---------------------------------------------------------------


class YtChannelLookupError(LookupError):
    """A reference did not name exactly one channel."""


class YtChannelNotFoundError(YtChannelLookupError):
    def __init__(self, ref: str) -> None:
        super().__init__(
            f"no YouTube channel {ref} in the inventory; add it with "
            "`itgraph yt add` first. A handle is found only while its "
            "channel's data is stored — give the UC… id if it expired"
        )
        self.ref = ref


class AmbiguousHandleError(YtChannelLookupError):
    """Two stored payloads carry one handle: one of them is stale."""

    def __init__(self, handle: str, channel_ids: Sequence[str]) -> None:
        super().__init__(
            f"@{handle} is carried by {len(channel_ids)} channels "
            f"({', '.join(channel_ids)}); give the id instead"
        )
        self.channel_ids = channel_ids


@dataclass(frozen=True, slots=True)
class Review:
    """A review decision, in the shape the file and `mark` both write.

    ``note`` is the rejection note on a rejection and the kind note
    otherwise — one column in the file, two in the table.
    """

    status: ChannelStatus
    kind: YtChannelKind | None = None
    lang: YtLang | None = None
    reason: YtRejectReason | None = None
    note: str | None = None

    def check(self) -> None:
        """Refuse a review the database would refuse, before writing it."""
        if (self.status is ChannelStatus.REJECTED) != (
            self.reason is not None
        ):
            raise ValueError(
                "a rejection needs a reason, and only a rejection may have one"
            )
        if self.status is ChannelStatus.SEED and (
            self.kind is None or self.lang is None
        ):
            raise ValueError("a seed needs a kind and a language")


def current_review(channel: YtChannel) -> Review:
    """A record's review as a ``Review``, comparable with a new one."""
    rejected = channel.status is ChannelStatus.REJECTED
    return Review(
        status=channel.status,
        kind=channel.kind,
        lang=channel.lang,
        reason=channel.reject_reason,
        note=channel.reject_note if rejected else channel.kind_note,
    )


def _write(channel: YtChannel, review: Review, now: datetime) -> None:
    channel.status = review.status
    channel.kind = review.kind
    channel.lang = review.lang
    if review.status is ChannelStatus.REJECTED:
        channel.reject_reason = review.reason
        channel.reject_note = review.note
    else:
        # Clearing the reason is what lets a rejected channel become a
        # seed later without tripping the check constraint.
        channel.reject_reason = None
        channel.reject_note = None
        channel.kind_note = review.note
    channel.reviewed_at = now


async def find_channel(session: AsyncSession, ref: str) -> YtChannel:
    """One channel by its ``UC…`` id, or by a handle its stored data carries.

    Handles match case-insensitively, with or without ``@``. A channel
    whose data expired has no handle to be found by until the next
    refresh; its id always works.
    """
    if CHANNEL_ID_SHAPE.match(ref):
        channel = await session.get(YtChannel, ref)
        if channel is None:
            raise YtChannelNotFoundError(ref)
        return channel

    handle = ref.removeprefix("@").lower()
    matches = (
        await session.scalars(
            select(YtChannel)
            .join(
                YtRawChannel, YtRawChannel.channel_id == YtChannel.channel_id
            )
            .where(stored_handle() == handle)
            .order_by(YtChannel.channel_id)
        )
    ).all()
    if not matches:
        raise YtChannelNotFoundError(f"@{handle}")
    if len(matches) > 1:
        raise AmbiguousHandleError(handle, [row.channel_id for row in matches])
    return matches[0]


async def mark_channel(
    session: AsyncSession,
    ref: str,
    review: Review,
    *,
    now: datetime | None = None,
) -> YtChannel:
    """Record one review decision, replacing whatever was decided before.

    The one way to change a decision already made: the review file never
    overwrites a decision it did not see.

    A label left out keeps its value — kind, language, and the kind note
    when the decision is not a rejection — the way Telegram's ``mark``
    keeps a kind it was not given. The command shows none of them, so an
    omission is never a choice to clear one, and a ``--maybe`` that erased
    a reviewer's kind would lose hand work nothing can rebuild. A
    rejection's note goes with its reason, as on Telegram: a new reason
    without a note has none. The review file is different on purpose: it
    shows the current values, so an emptied cell there means emptied.
    """
    channel = await find_channel(session, ref)
    kept = replace(
        review,
        kind=review.kind if review.kind is not None else channel.kind,
        lang=review.lang if review.lang is not None else channel.lang,
        note=(
            review.note
            if review.note is not None
            or review.status is ChannelStatus.REJECTED
            else channel.kind_note
        ),
    )
    kept.check()
    _write(channel, kept, now or datetime.now(UTC))
    return channel


@dataclass(slots=True)
class ApplySummary:
    applied: list[Decision] = field(default_factory=list)
    unchanged: list[Decision] = field(default_factory=list)
    conflicts: list[Decision] = field(default_factory=list)
    skipped: int = 0

    def line(self) -> str:
        return (
            f"applied {len(self.applied)}, unchanged {len(self.unchanged)}, "
            f"conflicts {len(self.conflicts)}, left blank {self.skipped}"
        )


def _line_number(problem: RowProblem) -> int:
    return int(problem.where.removeprefix("line "))


async def apply_review(
    session: AsyncSession,
    parsed: ParsedReview,
    *,
    now: datetime | None = None,
) -> ApplySummary:
    """Apply a review file's decisions, all in the caller's transaction.

    Every problem — the file's own and channels the inventory does not
    hold — is raised together before anything is written. Then each row:

    - equal to the record's current review: unchanged, no timestamp moves,
      which is what makes a second apply of the same file a no-op;
    - the record reviewed after the file was exported: a conflict, not
      written — someone decided something the file never saw;
    - otherwise written.
    """
    ids = [decision.channel_id for decision in parsed.decisions]
    records = {
        channel.channel_id: channel
        for channel in await session.scalars(
            select(YtChannel).where(YtChannel.channel_id.in_(ids))
        )
    }
    problems = list(parsed.problems) + [
        RowProblem(
            decision.where, f"{decision.channel_id} is not in the inventory"
        )
        for decision in parsed.decisions
        if decision.channel_id not in records
    ]
    if problems:
        raise ReviewFileError(sorted(problems, key=_line_number))

    moment = now or datetime.now(UTC)
    summary = ApplySummary(skipped=parsed.skipped)
    for decision in parsed.decisions:
        channel = records[decision.channel_id]
        review = Review(
            status=decision.status,
            kind=decision.kind,
            lang=decision.lang,
            reason=decision.reason,
            note=decision.note,
        )
        if current_review(channel) == review:
            summary.unchanged.append(decision)
        elif (
            channel.reviewed_at is not None
            and channel.reviewed_at > parsed.exported_at
        ):
            summary.conflicts.append(decision)
        else:
            _write(channel, review, moment)
            summary.applied.append(decision)
    return summary


# --- listing --------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class Listed:
    """A record, and what YouTube last said about it, if that is stored."""

    channel: YtChannel
    payload: Mapping[str, Any] | None


async def list_channels(
    session: AsyncSession,
    *,
    status: ChannelStatus | None = None,
    unreviewed: bool = False,
) -> list[Listed]:
    """The inventory with its stored data, oldest first."""
    statement = (
        select(YtChannel, YtRawChannel.payload)
        .outerjoin(
            YtRawChannel, YtRawChannel.channel_id == YtChannel.channel_id
        )
        .order_by(YtChannel.first_seen_at, YtChannel.channel_id)
    )
    if status is not None:
        statement = statement.where(YtChannel.status == status)
    if unreviewed:
        statement = statement.where(YtChannel.reviewed_at.is_(None))
    rows = await session.execute(statement)
    return [Listed(channel, payload) for channel, payload in rows.tuples()]


@dataclass(frozen=True, slots=True)
class InventorySummary:
    by_status: Mapping[ChannelStatus, int]
    seeds_by_lang: Mapping[YtLang, int]
    current: int
    expired: int
    gone: int


async def inventory_summary(session: AsyncSession) -> InventorySummary:
    """Review progress, and the state of the inventory's API data."""
    by_status = dict.fromkeys(ChannelStatus, 0)
    for status, total in await session.execute(
        select(YtChannel.status, func.count()).group_by(YtChannel.status)
    ):
        by_status[status] = total

    seeds_by_lang = dict.fromkeys(YtLang, 0)
    for lang, total in await session.execute(
        select(YtChannel.lang, func.count())
        .where(YtChannel.status == ChannelStatus.SEED)
        .group_by(YtChannel.lang)
    ):
        seeds_by_lang[lang] = total

    stored = YtRawChannel.channel_id.is_not(None)
    gone = YtChannel.gone_at.is_not(None)
    row = (
        await session.execute(
            select(
                func.count().filter(stored),
                func.count().filter(~stored, ~gone),
                func.count().filter(gone),
            )
            .select_from(YtChannel)
            .outerjoin(
                YtRawChannel, YtRawChannel.channel_id == YtChannel.channel_id
            )
        )
    ).one()
    return InventorySummary(
        by_status=by_status,
        seeds_by_lang=seeds_by_lang,
        current=row[0],
        expired=row[1],
        gone=row[2],
    )
