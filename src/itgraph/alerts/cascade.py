"""Which posts are travelling, measured from the derived edges alone.

Pure functions over plain rows — no session, no engine, no network, and
no clock of its own. Every input is passed in, including the moment to
reason from, so a test states a scenario instead of arranging for time to
pass. The same shape ``affiliation/signals.py`` has, and for the same
reason: what counts as a cascade is a question that will be re-asked over
data already collected.

**Distinct families, never reposts.** A channel that carries a post five
times is one source, and an author's five channels carrying it are also
one. That is the whole difference between measuring how far something
travelled and measuring how loudly one person said it, and it is why the
affiliation work is a prerequisite for this rather than a nicety.

The measured rates over this corpus, which is where the defaults come
from and why they are not round numbers picked for looking sensible:

    families reposting     alerts/day      over the last 30 and 60 days,
    one post, within 6h                    intra-family excluded
    ────────────────────────────────────────────────────────────────────
          1                   ~19          "somebody reposted this"
          2                   ~1.1
          3                   ~0.35
          4+                  ~0           one case in two months

There is almost no band between noise and silence, which is why the
bands stop at three: a fourth would be a line in a config file that
never fires and misleads whoever reads it next.
"""

from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass
from datetime import datetime, timedelta

__all__ = [
    "Cascade",
    "RepostEdge",
    "crossings",
    "family_counts",
]


@dataclass(frozen=True, slots=True)
class RepostEdge:
    """One observed repost, in the terms this measurement needs.

    A narrow projection of an ``edges`` row rather than the row itself:
    what a cascade is made of is *who* carried *what* and *when*, and
    passing the whole row would let this module start depending on
    columns it has no business reading.

    ``post_key`` identifies the referenced post after album collapsing —
    the parts of one album are one post here, and the caller has already
    resolved that. Doing it in the caller is deliberate: the grouping
    comes from ``raw_messages``, because ``edges.grouped_id`` describes
    the *referencing* message and says nothing about the album being
    referenced.
    """

    post_key: tuple[int, int]
    post_published_at: datetime
    post_family: int
    src_family: int
    reposted_at: datetime


@dataclass(frozen=True, slots=True)
class Cascade:
    """A post that crossed a band, and by how much.

    ``value`` is the family count at the moment it was measured, which is
    what the alert stores and what a later verdict is about. ``band`` is
    the threshold it crossed — the highest one it satisfies, so a post
    that arrives already at four families raises the bands beneath it
    too rather than skipping them.
    """

    post_key: tuple[int, int]
    band: int
    value: int


def family_counts(
    edges: Iterable[RepostEdge],
    *,
    now: datetime,
    window: timedelta,
) -> dict[tuple[int, int], set[int]]:
    """Which families carried each post, inside the window.

    Three exclusions, each removing a way to be wrong rather than a way
    to be uninteresting:

    A repost by the post's **own family** is distribution, not travel. A
    network run by one author carrying its own post across every channel
    it owns says nothing about anyone else finding it worth carrying.

    A repost **outside the window** is not counted, and the window is
    measured from the post's publication rather than from now. That is
    what makes this "travelling" rather than "has ever travelled" — a
    post that collects a second family after three days did travel, and
    is a line in a weekly summary rather than a message tonight.

    A repost **predating the post it refers to** is dropped. Clock skew
    and a wrong ``dst_published_at`` both produce these, and a negative
    age passes every window test silently, which is the worst way for
    bad data to behave.

    **And the post itself has to be inside the window**, which is the
    exclusion that is easiest to leave to the caller and wrong to. A post
    published a year ago whose reposts all arrived within an hour of it
    satisfies every age test above and is not travelling now by any
    reading. Leaving that to the loading query would make this function
    true only in company — and it is precisely the condition that
    removes the first-run problem, so it belongs where the rule is
    stated rather than where the rows happen to come from.
    """
    oldest_post = now - window
    carried: dict[tuple[int, int], set[int]] = {}
    for edge in edges:
        if edge.src_family == edge.post_family:
            continue
        if edge.post_published_at < oldest_post:
            continue
        age = edge.reposted_at - edge.post_published_at
        if age < timedelta(0) or age > window:
            continue
        carried.setdefault(edge.post_key, set()).add(edge.src_family)
    return carried


def crossings(
    edges: Iterable[RepostEdge],
    *,
    bands: Sequence[int],
    now: datetime,
    window: timedelta,
    already_raised: Mapping[tuple[int, int], set[int]] | None = None,
) -> list[Cascade]:
    """Every (post, band) pair that has been reached and not yet raised.

    Returns one entry per band a post satisfies, not one per post. A post
    that reaches four families crosses both configured bands and is
    entitled to both alerts — the second is what tells the operator it is
    still going, and skipping straight to the highest would lose that.

    ``already_raised`` is an optimisation and never a correctness
    mechanism: the unique constraint on ``alerts`` is what actually
    prevents a second alert, and this only avoids asking the database to
    reject rows it has rejected before. A caller that omits it gets the
    same alerts and a few more discarded inserts.

    Sorted by post and band so a run's output is stable, which is what
    makes its report readable and its tests writable without sorting at
    every assertion.
    """
    seen = already_raised or {}
    carried = family_counts(edges, now=now, window=window)

    found: list[Cascade] = []
    for post_key, families in carried.items():
        value = len(families)
        raised: frozenset[int] | set[int] = seen.get(post_key, frozenset())
        for band in bands:
            if value >= band and band not in raised:
                found.append(
                    Cascade(post_key=post_key, band=band, value=value)
                )

    return sorted(found, key=lambda entry: (entry.post_key, entry.band))
