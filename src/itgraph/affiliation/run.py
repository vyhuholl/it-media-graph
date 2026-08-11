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
from collections import defaultdict
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

__all__ = ["DetectionSummary", "HandleGroup", "run_detection"]

logger = logging.getLogger(__name__)


@dataclass(frozen=True, slots=True)
class HandleGroup:
    """The channels one named handle is asking about, and how many pairs.

    Computed over the *unbounded* review list, so a block the caller's
    limit truncates can still say how many of its pairs are not shown,
    and can still name every channel a confirmation would have to
    mention. Reading either off the truncated rows would quietly
    understate both.

    ``members`` holds each channel as the operator would type it — the
    username where there is one, the id otherwise — because what this
    exists for is a confirmation line they do not have to reassemble by
    hand.

    ``carriers`` is how many channels the handle names in total, which is
    usually more than ``members``: a channel whose every pair is already
    decided, or one in a pair with no seed on either side, carries the
    handle and is not being asked about. Both numbers are shown, because
    a block headed "2 channels" beside evidence reading ``handle:atom/4``
    otherwise looks like a miscount rather than a filter.
    """

    token: str
    pairs: int
    members: list[str]
    carriers: int


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
    # Keyed by handle, for the rows in `rows` that carry one.
    groups: dict[str, HandleGroup]

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
            # anywhere has not found nothing, it has looked nowhere. Two
            # signals read descriptions, so both are silent here and
            # naming one of them would misreport the other as having
            # looked and found nothing.
            lines.append(
                "  the description and named-handle signals produced "
                "nothing for lack of data — run `itgraph metadata` first"
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

    groups = _handle_groups(reviewable)
    return DetectionSummary(
        proposed=len(detection.candidates),
        awaiting_review=len(reviewable),
        channels_scored=detection.channels_scored,
        with_description=detection.with_description,
        refs_outside_inventory=detection.refs_outside_inventory,
        rows=rows,
        groups=groups,
    )


def _handle_groups(rows: list[CandidateRow]) -> dict[str, HandleGroup]:
    """The named-handle groups in a review list, keyed by handle.

    A member appears here only through a pair still awaiting review,
    which is the right membership for a confirmation line: a channel
    whose every pair is already decided is in the family already, and
    naming it again would ask for a decision that has been made.
    """
    pairs: dict[str, int] = defaultdict(int)
    members: dict[str, dict[int, str]] = defaultdict(dict)
    carriers: dict[str, int] = {}
    for row in rows:
        token = row.handle_token
        if token is None:
            continue
        pairs[token] += 1
        members[token][row.channel_a] = row.username_a or str(row.channel_a)
        members[token][row.channel_b] = row.username_b or str(row.channel_b)
        carriers[token] = row.handle_token_channels or 0
    return {
        token: HandleGroup(
            token=token,
            pairs=count,
            # By id, so the confirmation line is the same from run to run.
            members=[
                reference for _, reference in sorted(members[token].items())
            ],
            carriers=carriers[token],
        )
        for token, count in pairs.items()
    }
