"""Reads and writes of the YouTube affiliation tables: runs and pairs.

``db/affiliation.py``'s split, carried over: ``yt_affiliation_runs``
holds one row per detection run with the thresholds it used;
``yt_affiliation_candidates`` holds the pairs, their evidence, and the
operator's decision about each. A candidate points back at the run that
last measured it, which is what makes "under which thresholds was this
proposed" answerable without re-running anything.

Nothing here records that two channels share an owner — that is a review
decision and lives in ``db/yt_channels.py``. This module may only ever
propose, and read back what the confirmations imply.

Titles and handles are joined from ``yt_raw_channels.payload`` at read
time and stored nowhere: what YouTube said lives only in the raw layer,
so a candidate whose payload is gone prints ids and statuses only.
"""

from collections.abc import Iterable, Mapping
from dataclasses import dataclass

from sqlalchemy import Text, column, func, or_, select, table
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import aliased

from itgraph.db.models import (
    AffiliationDecision,
    ChannelStatus,
    YtAffiliationCandidate,
    YtAffiliationRun,
    YtChannel,
    YtRawChannel,
)
from itgraph.yt.affiliation.detect import Candidate, Detection, Inventory
from itgraph.yt.affiliation.signals import Thresholds, Weights

__all__ = [
    "CandidateRow",
    "count_candidates_by_decision",
    "family_keys",
    "family_of",
    "list_candidates",
    "load_inventory",
    "record_run",
    "upsert_candidates",
]


@dataclass(frozen=True, slots=True)
class CandidateRow:
    """One candidate joined to both channels, ready to print.

    The join happens in the query rather than at the call site: the
    output needs a title, a handle and a status for each side, and
    fetching them per row would be a round trip per pair for a list the
    operator reads in one screen. Title and handle come from the stored
    payload and are absent for a channel whose payload is gone.
    """

    channel_a: str
    channel_b: str
    score: float
    decision: AffiliationDecision
    title_a: str | None
    title_b: str | None
    handle_a: str | None
    handle_b: str | None
    status_a: ChannelStatus
    status_b: ChannelStatus
    ref_direction: str | None
    shared_token: str | None
    shared_token_channels: int | None
    corroborated_token: str | None
    corroborated_token_channels: int | None


async def family_keys(session: AsyncSession) -> dict[str, str]:
    """Which family each channel belongs to, for the channels in one.

    Read from the ``yt_channel_families`` view — the connected components
    of the confirmed pairs, computed rather than stored, so it cannot
    disagree with the pairs it comes from. A channel absent from the
    result is its own family of one; callers wanting that spelled out use
    :func:`family_of`.

    The key is the lexicographically smallest channel id in the set. A
    label for the component, not a channel with any standing: families
    have no main channel, which is the whole point of reading them this
    way.
    """
    rows = await session.execute(
        select(
            column("channel_id", Text), column("family_key", Text)
        ).select_from(table("yt_channel_families"))
    )
    return {channel_id: key for channel_id, key in rows.all()}


def family_of(keys: Mapping[str, str], channel_id: str) -> str:
    """The family of one channel: its key, or itself when it has none."""
    return keys.get(channel_id, channel_id)


async def load_inventory(session: AsyncSession) -> Inventory:
    """Everything detection reads, in three queries.

    Fetched whole rather than queried per pair: 887 channels is 394 000
    pairs, and the signals emit pairs instead of being asked about them.
    The corpus fits in memory by a wide margin, so this is the cheap half
    of the run.

    No status filter. A family link on a rejected or candidate channel
    is true, costs nothing, and matters as soon as that channel is
    accepted — the same reading ``db/affiliation.py`` made on Telegram,
    and the pairs a hidden link would have spared the operator are
    exactly the ones a status filter would have hidden forever.
    """
    known = set(await session.scalars(select(YtChannel.channel_id)))
    keys = await family_keys(session)
    families = {
        channel_id: family_of(keys, channel_id) for channel_id in known
    }

    payload_rows = (
        await session.execute(
            select(
                YtRawChannel.channel_id,
                YtRawChannel.payload["snippet"]["title"].astext,
                YtRawChannel.payload["snippet"]["description"].astext,
                func.lower(
                    func.ltrim(
                        YtRawChannel.payload["snippet"]["customUrl"].astext,
                        "@",
                    )
                ),
            )
        )
    ).all()

    titles: dict[str, str] = {}
    descriptions: dict[str, str] = {}
    handles: dict[str, set[str]] = {}
    for channel_id, title, description, handle in payload_rows:
        # customUrl is the only field the collector always gets back;
        # title and description can be absent, and a channel without a
        # title has no tokens to share.
        if title:
            titles[channel_id] = title
        if description:
            descriptions[channel_id] = description
        if handle:
            handles.setdefault(handle, set()).add(channel_id)

    return Inventory(
        titles=titles,
        descriptions=descriptions,
        handles={
            handle: frozenset(carriers) for handle, carriers in handles.items()
        },
        known_channels=frozenset(known),
        family_of=families,
    )


async def record_run(
    session: AsyncSession,
    detection: Detection,
    *,
    thresholds: Thresholds,
    weights: Weights,
) -> int:
    """Write the run's parameters and coverage, return its id.

    Called before the candidates so they can point at it. The coverage is
    stored rather than only printed: a run that proposed four pairs over
    90% description coverage and one that proposed four over 40% are
    different results, and only the denominator says which happened.
    """
    run = YtAffiliationRun(
        min_token_length=thresholds.min_token_length,
        max_token_channels=thresholds.max_token_channels,
        max_corroborated_channels=thresholds.max_corroborated_channels,
        weight_reference=weights.reference,
        weight_group=weights.group,
        weight_token=weights.token,
        channels_scored=detection.channels_scored,
        with_payload=detection.with_payload,
        with_description=detection.with_description,
        refs_outside_inventory=detection.refs_outside_inventory,
    )
    session.add(run)
    await session.flush()
    return run.id


async def upsert_candidates(
    session: AsyncSession, candidates: Iterable[Candidate], *, run_id: int
) -> int:
    """Insert new pairs, refresh the measurement of known ones.

    The decision columns are deliberately absent from the update set.
    Re-running detection — after a refresh, or under a different
    threshold — must refresh the score and the evidence while leaving
    every confirmation and rejection exactly where it was. That is what
    makes a threshold cheap to re-try: it can never cost a review already
    done.

    Evidence columns *are* overwritten, nulls included: a signal that no
    longer fires under the new thresholds must stop claiming it did.
    """
    rows = [
        {
            "channel_a": candidate.pair[0],
            "channel_b": candidate.pair[1],
            "score": candidate.score,
            "run_id": run_id,
            "ref_direction": candidate.ref_direction,
            "shared_token": candidate.shared_token,
            "shared_token_channels": candidate.shared_token_channels,
            "corroborated_token": candidate.corroborated_token,
            "corroborated_token_channels": candidate.corroborated_token_channels,
        }
        for candidate in candidates
    ]
    if not rows:
        return 0

    statement = insert(YtAffiliationCandidate).values(rows)
    await session.execute(
        statement.on_conflict_do_update(
            index_elements=[
                YtAffiliationCandidate.channel_a,
                YtAffiliationCandidate.channel_b,
            ],
            set_={
                "score": statement.excluded.score,
                "run_id": statement.excluded.run_id,
                "ref_direction": statement.excluded.ref_direction,
                "shared_token": statement.excluded.shared_token,
                "shared_token_channels": (
                    statement.excluded.shared_token_channels
                ),
                "corroborated_token": statement.excluded.corroborated_token,
                "corroborated_token_channels": (
                    statement.excluded.corroborated_token_channels
                ),
            },
        )
    )
    return len(rows)


async def list_candidates(
    session: AsyncSession,
    *,
    limit: int | None = None,
    include_decided: bool = False,
    seeds_only: bool = True,
) -> list[CandidateRow]:
    """Candidates ranked strongest first, with both channels' identity.

    Decided pairs are hidden by default and kept, never deleted — a
    rejection is the record that stops the same pair being proposed
    again, and it is only useful while it can still be read.

    ``seeds_only`` hides a pair in which neither channel is a seed. It
    filters the *reading*, not the detection: the pairs are still
    computed and still stored, because a channel accepted next week turns
    its pair into one worth reviewing without anything needing to be
    recomputed.
    """
    side_a = aliased(YtChannel)
    side_b = aliased(YtChannel)
    # The payload joins are outer on purpose: a channel YouTube stopped
    # returning has no payload, its record and review stay, and a pair
    # naming it prints ids and statuses only.
    raw_a = aliased(YtRawChannel)
    raw_b = aliased(YtRawChannel)
    query = (
        select(
            YtAffiliationCandidate,
            side_a.status,
            side_b.status,
            raw_a.payload["snippet"]["title"].astext,
            raw_b.payload["snippet"]["title"].astext,
            func.ltrim(raw_a.payload["snippet"]["customUrl"].astext, "@"),
            func.ltrim(raw_b.payload["snippet"]["customUrl"].astext, "@"),
        )
        .join(side_a, side_a.channel_id == YtAffiliationCandidate.channel_a)
        .join(side_b, side_b.channel_id == YtAffiliationCandidate.channel_b)
        .outerjoin(raw_a, raw_a.channel_id == YtAffiliationCandidate.channel_a)
        .outerjoin(raw_b, raw_b.channel_id == YtAffiliationCandidate.channel_b)
    )
    if not include_decided:
        query = query.where(
            YtAffiliationCandidate.decision == AffiliationDecision.PENDING
        )
    if seeds_only:
        query = query.where(
            or_(
                side_a.status == ChannelStatus.SEED,
                side_b.status == ChannelStatus.SEED,
            )
        )
    # Ties break on the pair, so a bounded run is reproducible rather
    # than dependent on physical row order.
    query = query.order_by(
        YtAffiliationCandidate.score.desc(),
        YtAffiliationCandidate.channel_a,
        YtAffiliationCandidate.channel_b,
    )
    if limit is not None:
        query = query.limit(limit)

    return [
        CandidateRow(
            channel_a=candidate.channel_a,
            channel_b=candidate.channel_b,
            score=candidate.score,
            decision=candidate.decision,
            title_a=title_a,
            title_b=title_b,
            handle_a=handle_a,
            handle_b=handle_b,
            status_a=status_a,
            status_b=status_b,
            ref_direction=(
                candidate.ref_direction.value
                if candidate.ref_direction is not None
                else None
            ),
            shared_token=candidate.shared_token,
            shared_token_channels=candidate.shared_token_channels,
            corroborated_token=candidate.corroborated_token,
            corroborated_token_channels=candidate.corroborated_token_channels,
        )
        for (
            candidate,
            status_a,
            status_b,
            title_a,
            title_b,
            handle_a,
            handle_b,
        ) in (await session.execute(query)).all()
    ]


async def count_candidates_by_decision(
    session: AsyncSession,
) -> dict[AffiliationDecision, int]:
    """How many pairs sit in each decision state."""
    rows = await session.execute(
        select(YtAffiliationCandidate.decision, func.count()).group_by(
            YtAffiliationCandidate.decision
        )
    )
    return {decision: count for decision, count in rows.all()}
