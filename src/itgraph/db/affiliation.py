"""Reads and writes of the affiliation tables: runs and candidate pairs.

Two tables, and the split between them is the point. ``affiliation_runs``
holds one row per detection run with the thresholds it used;
``affiliation_candidates`` holds the pairs, their evidence, and the
operator's decision about each. A candidate points back at the run that
last measured it, which is what makes "under which thresholds was this
proposed" answerable without re-running anything.

Nothing here writes ``channels.operator_id``. Recording that two channels
share an author is a review decision and lives in ``db/channels.py``;
this module may only ever propose.
"""

from collections.abc import Iterable, Sequence
from dataclasses import dataclass

from sqlalchemy import func, select
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import aliased

from itgraph.affiliation.detect import Candidate, Detection, Inventory
from itgraph.affiliation.signals import Thresholds, Weights
from itgraph.db.models import (
    AffiliationCandidate,
    AffiliationDecision,
    AffiliationRun,
    Channel,
    ChannelStatus,
    Edge,
    EdgeKind,
    RawChannel,
)

__all__ = [
    "CandidateRow",
    "count_candidates_by_decision",
    "list_candidates",
    "load_inventory",
    "record_run",
    "upsert_candidates",
]


@dataclass(frozen=True, slots=True)
class CandidateRow:
    """One candidate joined to both channels, ready to print.

    The join happens in the query rather than at the call site: the
    output needs a username, a title and a status for each side, and
    fetching them per row would be a hundred round-trips for a list the
    operator reads in one screen.
    """

    channel_a: int
    channel_b: int
    score: float
    decision: AffiliationDecision
    username_a: str | None
    username_b: str | None
    title_a: str | None
    title_b: str | None
    status_a: ChannelStatus
    status_b: ChannelStatus
    about_direction: str | None
    shared_token: str | None
    shared_token_channels: int | None
    out_share: float | None
    out_share_edges: int | None
    out_share_src: int | None
    edges_a_to_b: int | None
    edges_b_to_a: int | None


async def load_inventory(
    session: AsyncSession, *, edge_kinds: Sequence[EdgeKind]
) -> Inventory:
    """Everything detection reads, in four queries.

    Fetched whole rather than queried per pair: 504 channels is 127 000
    pairs, and the signals emit pairs instead of being asked about them.
    The corpus fits in memory by a wide margin — 15 651 edges, 500
    usernames, 195 descriptions — so this is the cheap half of the run.

    The edge kinds are applied here, at the only place edges are read, so
    both edge-based signals see exactly the same filtered counts.

    No status filter. 10 of 19 concentration candidates on the real
    inventory point at a channel that is not a seed and 2 at a rejected
    one; dropping them would remove most of the signal to spare the
    operator rows they can already see the status of. A family link on a
    non-seed is true, costs nothing, and matters as soon as that channel
    is accepted.
    """
    channel_rows = (
        await session.execute(
            select(
                Channel.tg_id,
                Channel.username,
                Channel.linked_to,
                Channel.operator_id,
            )
        )
    ).all()

    usernames: dict[int, str] = {}
    linked_to: dict[int, int] = {}
    family_of: dict[int, int] = {}
    known: set[int] = set()
    for tg_id, username, linked, operator in channel_rows:
        known.add(tg_id)
        if username:
            # Lowercased to agree with `normalize_username`, so a handle
            # parsed out of a description and a stored username match.
            usernames[tg_id] = username.lower()
        if linked is not None:
            linked_to[tg_id] = linked
        # The family key, the same expression the analysis uses: the
        # channel the pointer names, or the channel itself.
        family_of[tg_id] = operator if operator is not None else tg_id

    description_rows = (
        await session.execute(
            select(
                RawChannel.channel_id,
                RawChannel.payload["full_chat"]["about"].astext,
            )
        )
    ).all()
    descriptions = {
        channel_id: about for channel_id, about in description_rows if about
    }

    edge_rows = (
        await session.execute(
            select(Edge.src_channel_id, Edge.dst_channel_id, func.count())
            .where(Edge.kind.in_(edge_kinds))
            .group_by(Edge.src_channel_id, Edge.dst_channel_id)
        )
    ).all()
    edges = {(src, dst): count for src, dst, count in edge_rows}

    return Inventory(
        usernames=usernames,
        descriptions=descriptions,
        edges=edges,
        known_channels=frozenset(known),
        linked_to=linked_to,
        family_of=family_of,
    )


async def record_run(
    session: AsyncSession,
    detection: Detection,
    *,
    thresholds: Thresholds,
    weights: Weights,
    edge_kinds: Sequence[EdgeKind],
) -> int:
    """Write the run's parameters and coverage, return its id.

    Called before the candidates so they can point at it. The coverage is
    stored rather than only printed: a run that proposed four pairs over
    40% description coverage and one that proposed four over full
    coverage are different results, and only the denominator says which
    happened.
    """
    run = AffiliationRun(
        min_out_edges=thresholds.min_out_edges,
        max_share_min=thresholds.max_share_min,
        min_token_length=thresholds.min_token_length,
        max_token_channels=thresholds.max_token_channels,
        min_mutual_edges=thresholds.min_mutual_edges,
        edge_kinds=[kind.value for kind in edge_kinds],
        weight_about=weights.about,
        weight_token=weights.token,
        weight_share=weights.share,
        weight_mutual=weights.mutual,
        channels_scored=detection.channels_scored,
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
    Re-running detection — after more history, or under a different
    threshold — must refresh the score and the evidence while leaving
    every confirmation and rejection exactly where it was. That is what
    makes a threshold cheap to re-try: it can never cost a review already
    done.

    Evidence columns *are* overwritten, nulls included: a signal that no
    longer fires under the new thresholds must stop claiming it did.
    Anything else would leave a candidate displaying evidence the current
    parameters do not support.
    """
    rows = [
        {
            "channel_a": candidate.pair[0],
            "channel_b": candidate.pair[1],
            "score": candidate.score,
            "run_id": run_id,
            "about_direction": candidate.about_direction,
            "shared_token": candidate.shared_token,
            "shared_token_channels": candidate.shared_token_channels,
            "out_share": candidate.out_share,
            "out_share_edges": candidate.out_share_edges,
            "out_share_src": candidate.out_share_src,
            "edges_a_to_b": candidate.edges_a_to_b,
            "edges_b_to_a": candidate.edges_b_to_a,
        }
        for candidate in candidates
    ]
    if not rows:
        return 0

    statement = insert(AffiliationCandidate).values(rows)
    await session.execute(
        statement.on_conflict_do_update(
            index_elements=[
                AffiliationCandidate.channel_a,
                AffiliationCandidate.channel_b,
            ],
            set_={
                "score": statement.excluded.score,
                "run_id": statement.excluded.run_id,
                "about_direction": statement.excluded.about_direction,
                "shared_token": statement.excluded.shared_token,
                "shared_token_channels": (
                    statement.excluded.shared_token_channels
                ),
                "out_share": statement.excluded.out_share,
                "out_share_edges": statement.excluded.out_share_edges,
                "out_share_src": statement.excluded.out_share_src,
                "edges_a_to_b": statement.excluded.edges_a_to_b,
                "edges_b_to_a": statement.excluded.edges_b_to_a,
            },
        )
    )
    return len(rows)


async def list_candidates(
    session: AsyncSession,
    *,
    limit: int | None = None,
    include_decided: bool = False,
) -> list[CandidateRow]:
    """Candidates ranked strongest first, with both channels' identity.

    Decided pairs are hidden by default and kept, never deleted — a
    rejection is the record that stops the same pair being proposed
    again, and it is only useful while it can still be read.
    """
    side_a = aliased(Channel)
    side_b = aliased(Channel)
    query = (
        select(AffiliationCandidate, side_a, side_b)
        .join(side_a, side_a.tg_id == AffiliationCandidate.channel_a)
        .join(side_b, side_b.tg_id == AffiliationCandidate.channel_b)
    )
    if not include_decided:
        query = query.where(
            AffiliationCandidate.decision == AffiliationDecision.PENDING
        )
    # Ties break on the pair, so a bounded run is reproducible rather
    # than dependent on physical row order.
    query = query.order_by(
        AffiliationCandidate.score.desc(),
        AffiliationCandidate.channel_a,
        AffiliationCandidate.channel_b,
    )
    if limit is not None:
        query = query.limit(limit)

    return [
        CandidateRow(
            channel_a=candidate.channel_a,
            channel_b=candidate.channel_b,
            score=candidate.score,
            decision=candidate.decision,
            username_a=channel_a.username,
            username_b=channel_b.username,
            title_a=channel_a.title,
            title_b=channel_b.title,
            status_a=channel_a.status,
            status_b=channel_b.status,
            about_direction=(
                candidate.about_direction.value
                if candidate.about_direction is not None
                else None
            ),
            shared_token=candidate.shared_token,
            shared_token_channels=candidate.shared_token_channels,
            out_share=candidate.out_share,
            out_share_edges=candidate.out_share_edges,
            out_share_src=candidate.out_share_src,
            edges_a_to_b=candidate.edges_a_to_b,
            edges_b_to_a=candidate.edges_b_to_a,
        )
        for candidate, channel_a, channel_b in (
            await session.execute(query)
        ).all()
    ]


async def count_candidates_by_decision(
    session: AsyncSession,
) -> dict[AffiliationDecision, int]:
    """How many pairs sit in each decision state."""
    rows = await session.execute(
        select(AffiliationCandidate.decision, func.count()).group_by(
            AffiliationCandidate.decision
        )
    )
    return {decision: count for decision, count in rows.all()}
