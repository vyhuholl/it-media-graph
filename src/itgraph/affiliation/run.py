"""The detection pass: load, score, store, report.

The one place the pure signal computation meets a database. It reads the
inventory, the derived edges and the stored descriptions, runs the
signals over them in memory, and writes the proposals back. It makes no
network request and needs no session with Telegram — every input was
collected by an earlier pass, which is what makes a threshold cheap
enough to re-try on a whim.

What it may not do is decide. No path from here writes
``channels.operator_id``; that column is reached only through
``db/channels.py`` and only by an explicit confirmation.
"""

import logging
from dataclasses import dataclass

from itgraph.affiliation.detect import detect, validate_parameters
from itgraph.affiliation.signals import Thresholds, Weights
from itgraph.db.affiliation import (
    CandidateRow,
    list_candidates,
    load_inventory,
    record_run,
    upsert_candidates,
)
from itgraph.db.models import EdgeKind
from itgraph.db.session import Database

__all__ = ["DetectionSummary", "run_detection"]

logger = logging.getLogger(__name__)


@dataclass(frozen=True, slots=True)
class DetectionSummary:
    """What a detection run proposed, and what it could see.

    The coverage lines are not decoration. 302 of 504 seeds have no
    stored description, so the description signal is silent about most of
    the inventory — and a reader shown four candidates without the
    denominator would take that for four problems rather than four found
    among the two fifths it could look at.
    """

    proposed: int
    awaiting_review: int
    channels_scored: int
    with_description: int
    refs_outside_inventory: int
    rows: list[CandidateRow]

    def line(self) -> str:
        line = (
            f"{self.proposed} candidate pairs proposed over "
            f"{self.channels_scored} channels"
        )
        # The two numbers diverge for two different reasons — pairs
        # already decided, and pairs between channels not in scope — and
        # a reader who sees only the second would think the run found
        # less than it did.
        if self.awaiting_review != self.proposed:
            line += f", {self.awaiting_review} awaiting review"
        return line

    def coverage_lines(self) -> list[str]:
        without = self.channels_scored - self.with_description
        lines = [
            (
                f"descriptions: {self.with_description} of "
                f"{self.channels_scored} channels have one, "
                f"{without} do not"
            ),
        ]
        if self.with_description == 0:
            # Said in these words on purpose: a signal that could not run
            # anywhere has not found nothing, it has looked nowhere.
            lines.append(
                "  the description signal produced nothing for lack of "
                "data — run `itgraph metadata` first"
            )
        if self.refs_outside_inventory:
            lines.append(
                f"  {self.refs_outside_inventory} handles in descriptions "
                "name channels the inventory does not hold"
            )
        return lines


async def run_detection(
    database: Database,
    *,
    thresholds: Thresholds,
    weights: Weights,
    edge_kinds: list[EdgeKind],
    limit: int | None = None,
    include_decided: bool = False,
    seeds_only: bool = True,
) -> DetectionSummary:
    """Score every pair the signals propose, store them, read them back.

    Parameters are validated before anything is loaded, so a typo costs
    an error message rather than a run and a table full of proposals
    computed under a threshold nobody meant.

    ``seeds_only`` narrows what is *shown*, never what is computed or
    stored: a pair between two channels neither of which is in scope yet
    is still worth having on disk for the week one of them is accepted.
    """
    validate_parameters(
        thresholds, weights, [kind.value for kind in edge_kinds]
    )

    async with database.session() as session:
        inventory = await load_inventory(session, edge_kinds=edge_kinds)

    detection = detect(inventory, thresholds=thresholds, weights=weights)
    logger.info(
        "scored %d channels, %d proposals",
        detection.channels_scored,
        len(detection.candidates),
    )

    async with database.session() as session:
        run_id = await record_run(
            session,
            detection,
            thresholds=thresholds,
            weights=weights,
            edge_kinds=edge_kinds,
        )
        await upsert_candidates(session, detection.candidates, run_id=run_id)

    async with database.session() as session:
        # Unbounded first, so the count reported is what is reviewable
        # rather than what `--limit` happened to show.
        reviewable = await list_candidates(
            session,
            include_decided=include_decided,
            seeds_only=seeds_only,
        )
        rows = reviewable[:limit] if limit is not None else reviewable

    return DetectionSummary(
        proposed=len(detection.candidates),
        awaiting_review=len(reviewable),
        channels_scored=detection.channels_scored,
        with_description=detection.with_description,
        refs_outside_inventory=detection.refs_outside_inventory,
        rows=rows,
    )
