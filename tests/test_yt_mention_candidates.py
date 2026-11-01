"""The mention queue, pure: which channel each reference names, and the cut.

References are built by the mention graph's own reader from synthetic
text; lookups are plain mappings. Every id and handle is invented.
"""

from datetime import UTC, datetime

from ytfakes import channel_id, video_id

from itgraph.derive.yt_mention_candidates import (
    Naming,
    name_channels,
    ranking,
    report,
)
from itgraph.derive.yt_references import (
    Known,
    Pending,
    PendingKind,
    References,
    Source,
    read_text,
    resolve,
)

SEED_ONE = channel_id(1)
SEED_TWO = channel_id(2)
SEED_THREE = channel_id(3)
TARGET = channel_id(8)
PUBLISHED = datetime(2026, 9, 1, tzinfo=UTC)
INVENTORY = frozenset({SEED_ONE, SEED_TWO, SEED_THREE})
KNOWN = Known(handles={}, channels=INVENTORY, videos={})


def _read(
    *texts: tuple[str, str], featured: dict[str, list[str]] | None = None
) -> References:
    """Each (seed, text) as one of that seed's videos."""
    return resolve(
        [
            Source(
                video_id=video_id(100 + number),
                channel_id=seed,
                published_at=PUBLISHED,
                named=read_text(text),
            )
            for number, (seed, text) in enumerate(texts)
        ],
        KNOWN,
        featured,
    )


def _naming(
    references: References, lookups: dict[tuple[str, str], str | None]
) -> Naming:
    return name_channels(references, lookups)


# --- evidence is the seeds' videos -----------------------------------------


def test_a_handle_link_and_a_video_link_both_count() -> None:
    references = _read(
        (SEED_ONE, "youtube.com/@fake-unknown"),
        (SEED_TWO, f"youtu.be/{video_id(50)}"),
    )

    naming = _naming(
        references,
        {("handle", "fake-unknown"): TARGET, ("video", video_id(50)): TARGET},
    )

    assert naming.channels == {TARGET: {SEED_ONE, SEED_TWO}}


def test_a_featured_channel_is_not_evidence_here() -> None:
    references = _read(featured={SEED_ONE: [TARGET]})

    assert _naming(references, {}).channels == {}


# --- a reference names a channel through what is stored --------------------


def test_a_video_named_by_its_lookup() -> None:
    references = _read((SEED_ONE, f"youtu.be/{video_id(50)}"))

    naming = _naming(references, {("video", video_id(50)): TARGET})

    assert naming.channels == {TARGET: {SEED_ONE}}
    assert naming.awaiting == {}


def test_a_reference_not_yet_looked_up_awaits() -> None:
    references = _read((SEED_ONE, "youtube.com/@fake-unknown"))

    naming = _naming(references, {})

    assert naming.awaiting == {
        Pending(PendingKind.HANDLE, "fake-unknown"): frozenset({SEED_ONE})
    }
    assert naming.channels == {}


def test_a_reference_looked_up_as_not_found() -> None:
    references = _read((SEED_ONE, f"youtu.be/{video_id(50)}"))

    naming = _naming(references, {("video", video_id(50)): None})

    assert (naming.channels, naming.not_found) == ({}, 1)


def test_a_channel_id_is_its_own_answer() -> None:
    references = _read((SEED_ONE, f"youtube.com/channel/{TARGET}"))

    naming = _naming(references, {})

    assert naming.channels == {TARGET: {SEED_ONE}}
    assert naming.awaiting == {}


def test_what_awaits_is_ordered_by_seeds_then_kind_and_name() -> None:
    references = _read(
        (SEED_ONE, "youtube.com/@fake-b youtube.com/@fake-a"),
        (SEED_TWO, f"youtube.com/@fake-b youtu.be/{video_id(50)}"),
        (SEED_THREE, f"youtu.be/{video_id(50)}"),
    )

    names = [
        (name.kind.value, name.value)
        for name in _naming(references, {}).to_look_up()
    ]

    assert names == [
        ("handle", "fake-b"),
        ("video", video_id(50)),
        ("handle", "fake-a"),
    ]


# --- a reference to its own channel is dropped -----------------------------


def test_an_old_episode_of_the_same_channel() -> None:
    references = _read(
        (SEED_ONE, f"youtu.be/{video_id(50)}"),
        (SEED_TWO, f"youtu.be/{video_id(50)}"),
    )

    naming = _naming(references, {("video", video_id(50)): SEED_ONE})

    # The seed's own old video is no evidence; the other seed's link is.
    assert naming.channels == {SEED_ONE: {SEED_TWO}}
    assert naming.own == 1


# --- distinct seeds are the unit -------------------------------------------


def test_one_seed_referencing_repeatedly_is_one_seed() -> None:
    references = _read(
        (SEED_ONE, "youtube.com/@fake-unknown"),
        (SEED_ONE, f"youtu.be/{video_id(50)}"),
        (SEED_ONE, "youtube.com/@fake-unknown"),
    )

    naming = _naming(
        references,
        {("handle", "fake-unknown"): TARGET, ("video", video_id(50)): TARGET},
    )

    assert naming.channels == {TARGET: {SEED_ONE}}


# --- ranking -----------------------------------------------------------------


def _naming_of(seeds_by_channel: dict[str, set[str]]) -> Naming:
    naming = Naming()
    naming.channels = {
        channel: set(seeds) for channel, seeds in seeds_by_channel.items()
    }
    return naming


def test_more_seeds_rank_first_and_a_tie_goes_by_id() -> None:
    naming = _naming_of(
        {
            channel_id(9): {SEED_ONE, SEED_TWO},
            channel_id(8): {SEED_ONE, SEED_TWO},
            channel_id(7): {SEED_ONE, SEED_TWO, SEED_THREE},
        }
    )

    ranked = ranking(naming, INVENTORY)

    assert [row.channel_id for row in ranked] == [
        channel_id(7),
        channel_id(8),
        channel_id(9),
    ]


def test_known_channels_are_never_ranked() -> None:
    naming = _naming_of({SEED_TWO: {SEED_ONE, SEED_THREE}})

    assert ranking(naming, INVENTORY) == []


def test_one_seed_is_enough_when_asked() -> None:
    naming = _naming_of({TARGET: {SEED_ONE}})

    assert ranking(naming, INVENTORY) == []
    assert [
        row.channel_id for row in ranking(naming, INVENTORY, min_seeds=1)
    ] == [TARGET]


def test_the_report_shows_ids_and_counts_and_what_awaits() -> None:
    references = _read(
        (SEED_ONE, f"youtube.com/channel/{TARGET} youtube.com/@fake-unknown"),
        (SEED_TWO, f"youtube.com/channel/{TARGET}"),
    )
    naming = _naming(references, {})
    ranked = ranking(naming, INVENTORY)

    lines = report(references, naming, ranked, top=100)

    [row] = [line for line in lines if TARGET in line]
    assert "2 seeds" in row and SEED_ONE in row and SEED_TWO in row
    assert lines[-2] == (
        "awaiting a lookup: 1 references — run `itgraph yt mention-lookup`"
    )
    assert lines[-1] == "looked up, no channel: 0 references"
    assert "fake-unknown" not in "\n".join(lines)
