"""A rising offer's card, its buttons and a press, pure.

Names and ids are fake and spelled so.
"""

import string
from collections.abc import Sequence
from datetime import UTC, datetime

import pytest

from itgraph.bot.review import (
    BACK,
    KIND_LABELS,
    MAYBE,
    REASON_LABELS,
    REJECT,
    SEED,
    CardView,
    Decision,
    Keyboard,
    PressKind,
    SeedPost,
    decision_line,
    first_step,
    kinds,
    parse,
    reasons,
    render_card,
)
from itgraph.db.models import ChannelKind, ChannelStatus, RejectReason

OFFER = 9223372036854775807  # the largest id, for the longest data
FIRST = datetime(2026, 11, 9, 22, 30, tzinfo=UTC)  # 10 November in Moscow
POSTS = (
    SeedPost("fake_seed_a", None, 11),
    SeedPost("fake_seed_b", None, 22),
    SeedPost("fake_seed_c", None, 33),
)


def card(
    *,
    title: str | None = "Fake Rising",
    families: int = 3,
    families_before: int = 1,
    first_in_window: datetime | None = FIRST,
    posts: Sequence[SeedPost] = POSTS,
) -> str:
    return render_card(
        CardView(
            username="fake_rising",
            title=title,
            families=families,
            families_before=families_before,
            first_in_window=first_in_window,
            posts=posts,
        ),
        window_days=14,
        zone="Europe/Moscow",
    )


def buttons(keyboard: Keyboard) -> list[tuple[str, str]]:
    return [button for row in keyboard for button in row]


# --- the card ---------------------------------------------------------


def test_a_card_with_three_families_and_three_posts() -> None:
    assert card() == (
        '📈 Растёт: <a href="https://t.me/fake_rising">@fake_rising</a>\n'
        "<b>Fake Rising</b>\n"
        "3 семьи за 14 дней, 1 раньше · впервые в окне 10 ноября\n"
        'Ссылались: <a href="https://t.me/fake_seed_a/11">@fake_seed_a</a>, '
        '<a href="https://t.me/fake_seed_b/22">@fake_seed_b</a>, '
        '<a href="https://t.me/fake_seed_c/33">@fake_seed_c</a>'
    )


def test_a_seed_without_a_username_is_named_unlinked() -> None:
    text = card(posts=[SeedPost(None, "Fake <Seed>", 11)])

    assert text.endswith("Ссылались: Fake &lt;Seed&gt;")


def test_a_title_that_looks_like_markup_is_shown_as_written() -> None:
    text = card(title="<b>Fake & Co</b>")

    assert "<b>&lt;b&gt;Fake &amp; Co&lt;/b&gt;</b>" in text


@pytest.mark.parametrize(
    ("families", "said"),
    [(2, "2 семьи"), (5, "5 семей"), (11, "11 семей"), (21, "21 семья")],
)
def test_families_are_declined(families: int, said: str) -> None:
    assert f"{said} за 14 дней" in card(families=families)


def test_none_before_the_window() -> None:
    assert "раньше ни одной" in card(families_before=0)


def test_no_title_and_no_post() -> None:
    text = card(title=None, posts=[], first_in_window=None)

    assert text.splitlines() == [
        '📈 Растёт: <a href="https://t.me/fake_rising">@fake_rising</a>',
        "3 семьи за 14 дней, 1 раньше",
    ]


# --- the buttons ------------------------------------------------------

EVERY_LABEL = [
    SEED,
    MAYBE,
    REJECT,
    BACK,
    *KIND_LABELS.values(),
    *REASON_LABELS.values(),
]


@pytest.mark.parametrize("label", EVERY_LABEL)
def test_every_label_holds_to_the_rule(label: str) -> None:
    """One to three words, a leading emoji not counted, no trailing
    punctuation (`CLAUDE.md`)."""
    emoji, *words = label.split(" ")
    assert emoji and not any(char.isalnum() for char in emoji)
    assert 1 <= len(words) <= 3
    assert all(any(char.isalpha() for char in word) for word in words)
    assert label[-1] not in string.punctuation + "…"


def test_every_kind_and_reason_has_a_label() -> None:
    assert set(KIND_LABELS) == set(ChannelKind)
    assert set(REASON_LABELS) == set(RejectReason)


def test_the_first_step() -> None:
    assert [label for label, _ in buttons(first_step(OFFER))] == [
        SEED,
        MAYBE,
        REJECT,
    ]


@pytest.mark.parametrize("step", [kinds, reasons])
def test_a_second_step_is_two_a_row_then_back(step: object) -> None:
    assert callable(step)
    keyboard = step(OFFER)

    assert all(len(row) == 2 for row in keyboard[:-2])
    assert len(keyboard[-2]) == 1  # the seventh
    assert keyboard[-1] == ((BACK, f"rv:{OFFER}:back"),)


@pytest.mark.parametrize(
    "keyboard",
    [first_step(OFFER), kinds(OFFER), reasons(OFFER)],
    ids=["first", "kinds", "reasons"],
)
def test_every_datum_fits_and_reads_back(keyboard: Keyboard) -> None:
    for _, data in buttons(keyboard):
        assert len(data.encode()) <= 64
        press = parse(data)
        assert press is not None
        assert press.offer_id == OFFER


def test_what_each_press_decides() -> None:
    by_label = {
        label: parse(data)
        for keyboard in (first_step(1), kinds(1), reasons(1))
        for label, data in buttons(keyboard)
    }

    assert by_label[SEED] is not None and by_label[SEED].decision is None
    assert by_label[REJECT] is not None and by_label[REJECT].decision is None
    assert by_label[BACK] is not None and by_label[BACK].decision is None
    maybe = by_label[MAYBE]
    assert maybe is not None
    assert maybe.decision == Decision(ChannelStatus.MAYBE)
    media = by_label[KIND_LABELS[ChannelKind.MEDIA]]
    assert media is not None
    assert media.kind is PressKind.KIND
    assert media.decision == Decision(
        ChannelStatus.SEED, kind=ChannelKind.MEDIA
    )
    crypto = by_label[REASON_LABELS[RejectReason.CRYPTO]]
    assert crypto is not None
    assert crypto.decision == Decision(
        ChannelStatus.REJECTED, reason=RejectReason.CRYPTO
    )


@pytest.mark.parametrize(
    "data",
    [
        None,
        "",
        "useful:12",
        "rv:12",
        "rv:x:maybe",
        "rv:12:promote",
        "rv:12:maybe:extra",
        "rv:12:kind",
        "rv:12:kind:famous",
        "rv:12:reason:boring",
        "rv:" + "1" * 70 + ":maybe",
    ],
)
def test_anything_else_is_no_press(data: str | None) -> None:
    assert parse(data) is None


# --- the decision line ------------------------------------------------


@pytest.mark.parametrize(
    ("decision", "line"),
    [
        (
            Decision(ChannelStatus.SEED, kind=ChannelKind.MEDIA),
            "✅ В сиды · медиа",
        ),
        (
            Decision(ChannelStatus.SEED, kind=ChannelKind.PERSONAL),
            "✅ В сиды · личный",
        ),
        (Decision(ChannelStatus.MAYBE), "🤔 Может быть"),
        (
            Decision(ChannelStatus.REJECTED, reason=RejectReason.NOT_IT),
            "🚫 Отклонён · не про IT",
        ),
        (
            Decision(ChannelStatus.REJECTED, reason=RejectReason.CONTENT_FARM),
            "🚫 Отклонён · контент-ферма",
        ),
    ],
)
def test_the_line_a_decision_adds(decision: Decision, line: str) -> None:
    assert decision_line(decision) == line


@pytest.mark.parametrize("kind", list(ChannelKind))
def test_a_seed_of_each_kind_has_a_line(kind: ChannelKind) -> None:
    assert decision_line(Decision(ChannelStatus.SEED, kind=kind)).startswith(
        "✅ В сиды · "
    )


@pytest.mark.parametrize("reason", list(RejectReason))
def test_a_rejection_for_each_reason_has_a_line(reason: RejectReason) -> None:
    assert decision_line(
        Decision(ChannelStatus.REJECTED, reason=reason)
    ).startswith("🚫 Отклонён · ")
