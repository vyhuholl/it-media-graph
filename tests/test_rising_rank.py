"""The rising ranking, pure: rows and a moment in, the ranking out.

Ids and names are fake and spelled so. A seed's family is its own id
unless ``families`` says otherwise.
"""

from collections.abc import Mapping, Sequence
from datetime import UTC, datetime, timedelta

from itgraph.derive.rising import (
    ChannelTarget,
    LeftOut,
    NameSource,
    PendingName,
    Ranking,
    Reference,
    RisingRows,
    rank_rising,
)

NOW = datetime(2026, 11, 17, 12, tzinfo=UTC)
RECENT = NOW - timedelta(days=3)
OLD = NOW - timedelta(days=40)

SEED_A, SEED_B, SEED_C, SEED_D = 1000000001, 1000000002, 1000000003, 1000000004
OTHER_CANDIDATE = 1000000009
NEW = 2000000001
NEWER = 2000000002


def target(
    tg_id: int = NEW,
    *,
    username: str | None = "fake_rising",
    title: str | None = "Fake rising",
    is_chat: bool = False,
    resolved: bool = True,
    attempts: int = 0,
    not_channel: bool = False,
) -> ChannelTarget:
    return ChannelTarget(
        tg_id=tg_id,
        username=username,
        title=title,
        is_chat=is_chat,
        resolved=resolved,
        attempts=attempts,
        not_channel=not_channel,
    )


def rows(
    *,
    references: Sequence[Reference] = (),
    channels: Sequence[ChannelTarget] = (),
    names: Sequence[PendingName] = (),
    sources: Sequence[NameSource] = (),
    families: Mapping[int, int] | None = None,
    seeds: Sequence[int] = (SEED_A, SEED_B, SEED_C, SEED_D),
) -> RisingRows:
    return RisingRows(
        seeds=frozenset(seeds),
        families=families or {},
        channels={channel.tg_id: channel for channel in channels},
        references=list(references),
        names={name.username: name for name in names},
        sources=list(sources),
    )


def rank(data: RisingRows) -> Ranking:
    return rank_rising(data, NOW, window_days=14, min_families=2)


def refs(dst: int, *sources: int, at: datetime = RECENT) -> list[Reference]:
    return [Reference(src=src, dst=dst, at=at) for src in sources]


# --- what is rising ----------------------------------------------------


def test_two_families_in_the_window() -> None:
    ranking = rank(
        rows(references=refs(NEW, SEED_A, SEED_B), channels=[target()])
    )

    assert [item.tg_id for item in ranking.ranked] == [NEW]
    assert ranking.ranked[0].families == 2
    assert ranking.ranked[0].families_before == 0
    assert ranking.ranked[0].first_in_window == RECENT


def test_one_family_is_not_enough() -> None:
    ranking = rank(
        rows(
            references=refs(NEW, SEED_A, SEED_B),
            channels=[target()],
            families={SEED_A: SEED_A, SEED_B: SEED_A},
        )
    )

    assert ranking.ranked == []
    assert ranking.awaiting == []
    assert not ranking.left_out


def test_its_own_family_does_not_count() -> None:
    ranking = rank(
        rows(
            references=refs(NEW, SEED_A, SEED_B),
            channels=[target()],
            families={SEED_A: SEED_A, NEW: SEED_A},
        )
    )

    assert ranking.ranked == []


def test_only_seeds_count() -> None:
    ranking = rank(
        rows(
            references=refs(NEW, SEED_A, OTHER_CANDIDATE),
            channels=[target()],
        )
    )

    assert ranking.ranked == []


def test_old_references_do_not_make_it_rising() -> None:
    ranking = rank(
        rows(
            references=refs(NEW, SEED_A, SEED_B, SEED_C, at=OLD),
            channels=[target()],
        )
    )

    assert ranking.ranked == []


def test_a_reference_after_the_moment_is_not_read() -> None:
    ranking = rank(
        rows(
            references=[
                *refs(NEW, SEED_A),
                *refs(NEW, SEED_B, at=NOW + timedelta(hours=1)),
            ],
            channels=[target()],
        )
    )

    assert ranking.ranked == []


def test_a_pending_username_rises_by_its_sources_dates() -> None:
    ranking = rank(
        rows(
            names=[PendingName("fake_name")],
            sources=[
                NameSource("fake_name", SEED_A, first=OLD, last=RECENT),
                NameSource("fake_name", SEED_B, first=RECENT, last=RECENT),
            ],
        )
    )

    assert [item.username for item in ranking.awaiting] == ["fake_name"]
    assert ranking.awaiting[0].tg_id is None
    assert ranking.awaiting[0].families == 2
    # First mentioned by one of them before the window.
    assert ranking.awaiting[0].families_before == 1


def test_a_source_without_times_is_no_evidence() -> None:
    ranking = rank(
        rows(
            names=[PendingName("fake_name")],
            sources=[
                NameSource("fake_name", SEED_A, first=RECENT, last=RECENT),
                NameSource("fake_name", SEED_B, first=None, last=None),
            ],
        )
    )

    assert ranking.awaiting == []


def test_a_name_last_mentioned_before_the_window_is_not_rising() -> None:
    ranking = rank(
        rows(
            names=[PendingName("fake_name")],
            sources=[
                NameSource("fake_name", seed, first=OLD, last=OLD)
                for seed in (SEED_A, SEED_B, SEED_C)
            ],
        )
    )

    assert ranking.awaiting == []


# --- how rising candidates rank -----------------------------------------


def test_more_families_first() -> None:
    ranking = rank(
        rows(
            references=[
                *refs(NEW, SEED_A, SEED_B),
                *refs(NEWER, SEED_A, SEED_B, SEED_C),
            ],
            channels=[target(NEW), target(NEWER, username="fake_newer")],
        )
    )

    assert [item.tg_id for item in ranking.ranked] == [NEWER, NEW]


def test_the_newer_comes_first() -> None:
    ranking = rank(
        rows(
            references=[
                *refs(NEW, SEED_A, SEED_B),
                *refs(NEW, SEED_A, SEED_B, SEED_C, SEED_D, at=OLD),
                *refs(NEWER, SEED_A, SEED_B),
            ],
            channels=[target(NEW), target(NEWER, username="fake_newer")],
        )
    )

    assert [item.tg_id for item in ranking.ranked] == [NEWER, NEW]
    assert ranking.ranked[1].families_before == 4


def test_a_tie_goes_to_a_fixed_order() -> None:
    """Channels by id, then names by name, whatever order rows came in."""
    data = rows(
        references=[*refs(NEWER, SEED_A, SEED_B), *refs(NEW, SEED_A, SEED_B)],
        channels=[
            target(NEWER, username=None, resolved=False),
            target(NEW, username=None, resolved=False),
        ],
        names=[PendingName("fake_b"), PendingName("fake_a")],
        sources=[
            NameSource(name, seed, first=RECENT, last=RECENT)
            for name in ("fake_b", "fake_a")
            for seed in (SEED_A, SEED_B)
        ],
    )

    awaiting = rank(data).awaiting

    assert [(item.tg_id, item.username) for item in awaiting] == [
        (NEW, None),
        (NEWER, None),
        (None, "fake_a"),
        (None, "fake_b"),
    ]


# --- what is left out ----------------------------------------------------


def test_a_supergroup_is_left_out() -> None:
    ranking = rank(
        rows(
            references=refs(NEW, SEED_A, SEED_B),
            channels=[target(is_chat=True)],
        )
    )

    assert ranking.ranked == []
    assert ranking.left_out == {LeftOut.CHAT: 1}


def test_a_channel_without_a_public_username_is_left_out() -> None:
    ranking = rank(
        rows(
            references=refs(NEW, SEED_A, SEED_B),
            channels=[target(username=None, resolved=True)],
        )
    )

    assert ranking.ranked == []
    assert ranking.awaiting == []
    assert ranking.left_out == {LeftOut.NO_USERNAME: 1}


def test_a_name_that_is_not_a_channel_is_left_out() -> None:
    ranking = rank(
        rows(
            names=[PendingName("fake_person", attempts=1, not_channel=True)],
            sources=[
                NameSource("fake_person", seed, first=RECENT, last=RECENT)
                for seed in (SEED_A, SEED_B)
            ],
        )
    )

    assert ranking.awaiting == []
    assert ranking.left_out == {LeftOut.NOT_CHANNEL: 1}


def test_a_failed_resolution_is_left_out_and_not_awaiting() -> None:
    ranking = rank(
        rows(
            references=refs(NEW, SEED_A, SEED_B),
            channels=[target(username=None, resolved=False, attempts=1)],
        )
    )

    assert ranking.awaiting == []
    assert ranking.left_out == {LeftOut.FAILED: 1}


def test_a_channel_never_resolved_awaits() -> None:
    ranking = rank(
        rows(
            references=refs(NEW, SEED_A, SEED_B),
            channels=[target(username=None, resolved=False)],
        )
    )

    assert [item.tg_id for item in ranking.awaiting] == [NEW]
    assert ranking.ranked == []
    assert not ranking.left_out


def test_a_reviewed_channel_is_neither_ranked_nor_left_out() -> None:
    """The loader hands over candidates and maybes only."""
    ranking = rank(
        rows(references=refs(NEW, SEED_A, SEED_B, SEED_C), channels=[])
    )

    assert ranking.ranked == []
    assert ranking.awaiting == []
    assert not ranking.left_out
