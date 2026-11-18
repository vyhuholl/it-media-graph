"""The YouTube inventory: every read and write of ``yt_channels`` and
``yt_raw_channels``.

Two tables because they hold two kinds of thing. A record is the
operator's — the id, the review, the provenance — and is never deleted.
A payload is YouTube's — title, handle, statistics: a refresh replaces
it, and a channel YouTube stops returning loses it. Nothing here copies
a payload field into a record, so a payload is the one place what
YouTube said about a channel lives.
"""

from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass, field, replace
from datetime import UTC, datetime
from typing import Any

from sqlalchemy import (
    ColumnElement,
    ColumnExpressionArgument,
    and_,
    delete,
    func,
    or_,
    select,
    update,
)
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import aliased

from itgraph.db.models import (
    AffiliationDecision,
    CandidateOrigin,
    ChannelStatus,
    YtAffiliationCandidate,
    YtChannel,
    YtChannelKind,
    YtDiscoverySource,
    YtLang,
    YtRawChannel,
    YtRejectReason,
)
from itgraph.db.yt_affiliation import family_keys, family_of
from itgraph.db.yt_backfill import forget_walks
from itgraph.yt.affiliation.signals import Pair, ordered
from itgraph.yt_entries import CHANNEL_ID as CHANNEL_ID_SHAPE
from itgraph.yt_review import (
    Decision,
    ParsedReview,
    ReviewFileError,
    RowProblem,
)

__all__ = [
    "AmbiguousHandleError",
    "ApplySummary",
    "ConfirmedGroup",
    "InventorySummary",
    "Listed",
    "Payload",
    "Review",
    "YtChannelLookupError",
    "YtChannelNotFoundError",
    "apply_review",
    "confirm_affiliation",
    "count_shown_pending",
    "create_channels",
    "current_review",
    "due_for_refresh",
    "find_channel",
    "ids_by_handle",
    "inventory_ids",
    "inventory_summary",
    "known_ids",
    "list_channels",
    "mark_channel",
    "mark_gone",
    "reject_affiliation",
    "reject_all",
    "reject_token",
    "store_payloads",
    "stored_handle",
    "withdraw_affiliation",
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


async def inventory_ids(session: AsyncSession) -> frozenset[str]:
    """Every channel id with a record, whatever its status, gone included.

    What every review queue leaves out: a record is never deleted, so a
    rejected channel is never proposed again.
    """
    return frozenset(await session.scalars(select(YtChannel.channel_id)))


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

    Only what is stored now: a channel whose payload is not stored costs
    one unit to recognise again.
    """
    wanted = [handle.lower() for handle in handles]
    if not wanted:
        return {}
    handle = stored_handle()
    rows = await session.execute(
        select(handle, YtRawChannel.channel_id).where(handle.in_(wanted))
    )
    return {found: channel_id for found, channel_id in rows.tuples()}


async def store_payloads(
    session: AsyncSession,
    payloads: Mapping[str, Payload],
    *,
    fetched_at: datetime,
) -> list[str]:
    """Store what YouTube just returned, replacing what was there.

    Replaced, never added to. A channel that had been marked gone and is
    returned again is restored. Returns the ids of those restored.
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

    The payload goes at once; the record and its review stay. So does
    any history walk of it: its videos are presumably gone too, and a
    channel that comes back is walked anew. Returns the ids newly marked
    — one already gone keeps its first date.
    """
    ids = list(channel_ids)
    if not ids:
        return []
    await session.execute(
        delete(YtRawChannel).where(YtRawChannel.channel_id.in_(ids))
    )
    await forget_walks(session, ids)
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
    a cycle. Gone channels come back here too, which is how one that
    returns is noticed.
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
            "channel's data is stored — give the UC… id if it is not"
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


async def _write(
    session: AsyncSession, channel: YtChannel, review: Review, now: datetime
) -> None:
    if (
        channel.status is ChannelStatus.SEED
        and review.status is not ChannelStatus.SEED
    ):
        # Out of scope, its history stops being kept current; a walk
        # record left behind would keep it from being walked again when
        # the channel comes back, and the uploads of the time between
        # would be missed.
        await forget_walks(session, [channel.channel_id])
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
    whose data is not stored has no handle to be found by until the next
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
    await _write(session, channel, kept, now or datetime.now(UTC))
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
            await _write(session, channel, review, moment)
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
    unfetched: int
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
        unfetched=row[1],
        gone=row[2],
    )


# --- affiliation review -----------------------------------------------------


@dataclass(frozen=True, slots=True)
class ConfirmedGroup:
    """What one confirmation statement recorded, and the family it made.

    The family can be larger than what was named, when a pair bridged
    two of them; the counts say so without the operator having to check.
    """

    pairs: int
    family: str
    channels: int


async def confirm_affiliation(
    session: AsyncSession,
    channels: Sequence[str],
    *,
    note: str | None = None,
) -> ConfirmedGroup:
    """Record that these channels share an owner.

    ``db/channels.py``'s contract, unchanged: two or more, every pair
    among them (not a chain — withdrawing a chain's middle link would
    split a family the operator asserted as whole), no canonical
    channel, and merging is what confirming a bridging pair means.

    Channels are named by ``UC…`` id or by a handle stored data carries;
    :func:`find_channel` resolves them, and an ambiguous handle or an
    unknown name fails writing nothing.
    """
    resolved = [await find_channel(session, ref) for ref in channels]
    if len(resolved) < 2:
        raise ValueError("name at least two channels")

    ids = [channel.channel_id for channel in resolved]
    if len(set(ids)) != len(ids):
        raise ValueError(
            "a channel cannot be affiliated with itself; "
            "the same channel is named more than once"
        )

    pairs = [
        ordered(first, second)
        for index, first in enumerate(ids)
        for second in ids[index + 1 :]
    ]
    for pair in pairs:
        await _record_decision(
            session,
            pair=pair,
            decision=AffiliationDecision.CONFIRMED,
            note=note,
        )

    # Read the family back rather than predicting it: a bridging pair
    # merges whole families, so the group the operator named can be
    # smaller than the family they just created, and the difference is
    # the thing worth showing them.
    await session.flush()
    keys = await family_keys(session)
    key = family_of(keys, ids[0])
    return ConfirmedGroup(
        pairs=len(pairs),
        family=key,
        channels=sum(1 for value in keys.values() if value == key) or 1,
    )


async def reject_affiliation(
    session: AsyncSession,
    first: str,
    second: str,
    *,
    note: str | None = None,
) -> Pair:
    """Record that two channels do not share an owner.

    A statement about a pair, which is why it takes exactly two. The row
    is what stops the same pair being proposed at every subsequent run,
    which is the only reason a rejection is stored — so it is kept and
    never deleted.
    """
    left = await find_channel(session, first)
    right = await find_channel(session, second)
    if left.channel_id == right.channel_id:
        raise ValueError("a channel cannot be affiliated with itself")

    pair = ordered(left.channel_id, right.channel_id)
    await _record_decision(
        session, pair=pair, decision=AffiliationDecision.REJECTED, note=note
    )
    return pair


async def reject_token(
    session: AsyncSession,
    token: str,
    *,
    note: str | None = None,
) -> int:
    """Reject every pending pair one corroborated token proposed.

    A noisy group costs one review instead of one per pair: a name
    token corroborated by cross-links (`david`, on the corpus) proposes
    a block of pairs of which most are real and a few are strangers.

    Pairs already decided are untouched — a rejection never rewrites a
    decision, in either direction. Pairs a later run proposes between
    the token's carriers and a new one are new questions and get
    proposed; the rejection stays pair-scoped, as every rejection is.

    Returns how many pairs were rejected, because that count is the
    whole point of the command: it is what one statement saved. Counted
    before the update rather than off its row count: this is an
    operator's command, nothing else writes between the two, and the
    typed result of an ``update`` carries no count to read anyway.
    """
    count = await session.scalar(
        select(func.count())
        .select_from(YtAffiliationCandidate)
        .where(
            YtAffiliationCandidate.decision == AffiliationDecision.PENDING,
            YtAffiliationCandidate.corroborated_token == token,
        )
    )
    if not count:
        return 0
    await session.execute(
        update(YtAffiliationCandidate)
        .where(
            YtAffiliationCandidate.decision == AffiliationDecision.PENDING,
            YtAffiliationCandidate.corroborated_token == token,
        )
        .values(
            decision=AffiliationDecision.REJECTED,
            decided_at=datetime.now(UTC),
            decision_note=note if note is not None else f"token '{token}'",
        )
    )
    return int(count)


def _shown_pending(
    side_a: type[YtChannel], side_b: type[YtChannel]
) -> ColumnExpressionArgument[bool]:
    """A pair the review list shows, as one predicate: the pair's own
    channels joined in, the decision pending, a seed on at least one side.

    ``list_candidates``'s ``seeds_only`` reading, as a predicate. The
    bulk rejection answers the list the operator reads, and this helper
    is what keeps "what the list shows" and "what one statement rejects"
    the same set. The joins live in the predicate rather than the query
    because the UPDATE side has no ``join()``: ``UPDATE ... FROM``
    arrives through the WHERE clause, and a predicate without them
    would cross-join.
    """
    return and_(
        side_a.channel_id == YtAffiliationCandidate.channel_a,
        side_b.channel_id == YtAffiliationCandidate.channel_b,
        YtAffiliationCandidate.decision == AffiliationDecision.PENDING,
        or_(
            side_a.status == ChannelStatus.SEED,
            side_b.status == ChannelStatus.SEED,
        ),
    )


async def count_shown_pending(session: AsyncSession) -> int:
    """How many pairs the review list currently shows.

    Asked before any prompting: the confirmation is informed or it is
    theater, and an empty list has nothing to confirm.
    """
    side_a = aliased(YtChannel)
    side_b = aliased(YtChannel)
    return int(
        await session.scalar(
            select(func.count())
            .select_from(YtAffiliationCandidate, side_a, side_b)
            .where(_shown_pending(side_a, side_b))
        )
        or 0
    )


async def reject_all(session: AsyncSession, *, note: str | None = None) -> int:
    """Reject every pair the review list shows, as one decision.

    ``reject_token`` with the token removed: the first detection run
    over the seeds proposed far more noise than families — blocks a
    token cap could not split — and the operator's answer was one
    statement clearing the list, not a review per pair. Everything the
    list does not show — hidden pairs on which neither channel is a
    seed, every decided pair, whatever token proposed it — is left
    exactly as it was, and the rejection stays pair-scoped, so a later
    run's new proposals are new questions.

    Returns how many pairs were rejected, counted before the update
    rather than off its row count: the number is the command's whole
    point, and `reject_token`'s note on that applies here unchanged.
    """
    count = await count_shown_pending(session)
    if not count:
        return 0
    side_a = aliased(YtChannel)
    side_b = aliased(YtChannel)
    await session.execute(
        update(YtAffiliationCandidate)
        .where(_shown_pending(side_a, side_b))
        .values(
            decision=AffiliationDecision.REJECTED,
            decided_at=datetime.now(UTC),
            decision_note=note if note is not None else "--reject-all",
        )
    )
    return count


async def withdraw_affiliation(
    session: AsyncSession, first: str, second: str
) -> Pair:
    """Undo a decision: the pair goes back to awaiting review.

    Nothing else happens, and nothing else needs to. The family is the
    connected components of the confirmed pairs, so removing one either
    changes nothing — because another chain still connects the two — or
    splits the family in exactly the place the withdrawn pair was
    holding together.
    """
    left = await find_channel(session, first)
    right = await find_channel(session, second)
    pair = ordered(left.channel_id, right.channel_id)

    await session.execute(
        update(YtAffiliationCandidate)
        .where(
            YtAffiliationCandidate.channel_a == pair[0],
            YtAffiliationCandidate.channel_b == pair[1],
        )
        .values(
            decision=AffiliationDecision.PENDING,
            decided_at=None,
            decision_note=None,
        )
    )
    return pair


async def _record_decision(
    session: AsyncSession,
    *,
    pair: Pair,
    decision: AffiliationDecision,
    note: str | None,
) -> None:
    """Write the review outcome onto the candidate, creating it if new.

    A pair no signal ever proposed is still recordable — the operator
    may simply know two channels share an owner. Such a row is marked as
    having come from the operator rather than from a signal, so "how
    much did detection actually catch" stays answerable.
    """
    values = {
        "channel_a": pair[0],
        "channel_b": pair[1],
        "score": 0.0,
        "origin": CandidateOrigin.OPERATOR,
        "decision": decision,
        "decided_at": datetime.now(UTC),
        "decision_note": note,
    }
    statement = insert(YtAffiliationCandidate).values([values])
    await session.execute(
        statement.on_conflict_do_update(
            index_elements=[
                YtAffiliationCandidate.channel_a,
                YtAffiliationCandidate.channel_b,
            ],
            set_={
                "decision": statement.excluded.decision,
                "decided_at": statement.excluded.decided_at,
                "decision_note": statement.excluded.decision_note,
            },
        )
    )
