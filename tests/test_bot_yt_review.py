"""A YouTube rising offer's card, its buttons and a press, pure.

Ids, handles and titles are fake and spelled so.
"""

import string
from datetime import UTC, datetime

import pytest

from itgraph.bot.yt_review import (
    BACK,
    KIND_LABELS,
    LANGUAGE_LABELS,
    MAYBE,
    REASON_LABELS,
    REJECT,
    SEED,
    ChannelView,
    Decision,
    Keyboard,
    LinkCardView,
    Press,
    PressKind,
    channel_link,
    decision_line,
    first_step,
    kinds,
    languages,
    parse,
    read_channel,
    reasons,
    render_link_card,
)
from itgraph.db.models import (
    ChannelStatus,
    YtChannelKind,
    YtLang,
    YtRejectReason,
)

OFFER = 9223372036854775807  # the largest id, for the longest data
FIRST = datetime(2026, 11, 9, 22, 30, tzinfo=UTC)  # 10 November in Moscow
CHANNEL = "UCfakeRisingLinks0000001"


def buttons(keyboard: Keyboard) -> list[tuple[str, str]]:
    return [button for row in keyboard for button in row]


# --- the card ---------------------------------------------------------


def view(
    *,
    handle: str | None = "@fake_handle",
    title: str | None = "Fake <Rising> & Co",
    language: str | None = None,
) -> ChannelView:
    return ChannelView(
        handle=handle.removeprefix("@") if handle else None,
        title=title,
        language=language,
    )


def link_card(
    channel: ChannelView | None = None,
    *,
    families: int = 3,
    families_before: int = 1,
) -> str:
    return render_link_card(
        LinkCardView(
            channel_id=CHANNEL,
            channel=channel or view(),
            families=families,
            families_before=families_before,
            first_in_window=FIRST,
        ),
        window_days=14,
        zone="Europe/Moscow",
    )


def test_a_link_card_with_a_payload() -> None:
    assert link_card() == (
        '📈 Растёт: <a href="https://www.youtube.com/@fake_handle">'
        "Fake &lt;Rising&gt; &amp; Co</a>\n"
        "3 семьи за 14 дней, 1 раньше · впервые в окне 10 ноября\n"
        "Язык: не определён"
    )


def test_a_card_without_a_payload_links_by_id_and_marks_nothing() -> None:
    text = link_card(view(handle=None, title=None, language=None))

    first = (
        '📈 Растёт: <a href="https://www.youtube.com/channel/'
        f'{CHANNEL}">{CHANNEL}</a>'
    )
    assert text.splitlines() == [
        first,
        "3 семьи за 14 дней, 1 раньше · впервые в окне 10 ноября",
        "Язык: не определён",
    ]


def test_a_known_language_of_a_topic_is_not_marked() -> None:
    for language in ("en", "ru"):
        assert "Язык" not in link_card(view(language=language))


def test_another_language_is_marked_as_its_own() -> None:
    assert "Язык: de" in link_card(view(language="de"))


def test_links_carry_nothing_unescaped() -> None:
    nasty = ChannelView(handle='fake_"onclick', title=None, language=None)
    url = channel_link(nasty, "<id>&")

    assert url == 'https://www.youtube.com/@fake_"onclick'


def test_a_payload_is_read_defensively() -> None:
    assert read_channel(None) == ChannelView()
    assert read_channel({}) == ChannelView()
    assert read_channel({"snippet": "not a mapping"}) == ChannelView()
    said = read_channel(
        {
            "snippet": {
                "customUrl": "@FakeHandle",
                "title": 42,
                "defaultLanguage": "de",
            }
        }
    )
    assert said.handle == "FakeHandle"
    assert said.title is None
    assert said.language == "de"


# --- the buttons ------------------------------------------------------

EVERY_LABEL = [
    SEED,
    MAYBE,
    REJECT,
    BACK,
    *KIND_LABELS.values(),
    *REASON_LABELS.values(),
    *LANGUAGE_LABELS.values(),
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


def test_every_kind_reason_and_language_has_a_label() -> None:
    assert set(KIND_LABELS) == set(YtChannelKind)
    assert set(REASON_LABELS) == set(YtRejectReason)
    assert set(LANGUAGE_LABELS) == set(YtLang)


def test_the_first_step_is_the_three() -> None:
    assert [label for label, _ in buttons(first_step(OFFER))] == [
        "🌱 В сиды",
        "🤔 Может быть",
        "🚫 Отклонить",
    ]
    by_label = {
        label: parse(data) for label, data in buttons(first_step(OFFER))
    }
    for opening in (SEED, REJECT):
        assert by_label[opening] is not None
        assert by_label[opening].decision() is None
    # Maybe alone is a decision.
    assert by_label[MAYBE].decision() == Decision(ChannelStatus.MAYBE)


def test_the_kinds_and_reasons_carry_back() -> None:
    for keyboard, value_of in (
        (kinds(OFFER), YtChannelKind),
        (reasons(OFFER), YtRejectReason),
    ):
        *choices, back = buttons(keyboard)
        assert back == (BACK, f"yv:{OFFER}:back")
        for _, data in choices:
            press = parse(data)
            assert press is not None
            assert isinstance(press.value, value_of)


def test_the_languages_carry_the_kind() -> None:
    keyboard = languages(OFFER, YtChannelKind.EDUCATION)

    assert [label for label, _ in buttons(keyboard)] == [
        "🇬🇧 EN",
        "🇷🇺 RU",
        "🌐 Другой",
        "↩️ Назад",
    ]
    presses = [parse(data) for _, data in buttons(keyboard)]
    assert presses[:-1] == [
        Press(OFFER, PressKind.LANG, (YtChannelKind.EDUCATION, YtLang.EN)),
        Press(OFFER, PressKind.LANG, (YtChannelKind.EDUCATION, YtLang.RU)),
        Press(OFFER, PressKind.LANG, (YtChannelKind.EDUCATION, YtLang.OTHER)),
    ]
    assert presses[-1] == Press(OFFER, PressKind.BACK)


# --- a press ----------------------------------------------------------


def test_a_press_round_trips_within_the_limit() -> None:
    longest = f"yv:{OFFER}:lang:education:other"
    assert len(longest.encode()) <= 64
    press = parse(longest)
    assert press == Press(
        OFFER,
        PressKind.LANG,
        (YtChannelKind.EDUCATION, YtLang.OTHER),
    )


@pytest.mark.parametrize(
    "data",
    [
        "",
        None,
        "rv:1:maybe",
        "yv:one:maybe",
        "yv:1:nope",
        "yv:1:kind",
        "yv:1:lang:media",
        "yv:1:kind:media:extra",
        "yv:1:reason:not_a_reason",
        "yv:1:kind:not_a_kind",
        "yv:1:lang:media:xx",
    ],
)
def test_what_is_not_a_press_is_not_parsed(data: str | None) -> None:
    assert parse(data) is None


def test_a_kind_press_decides_only_with_a_known_language() -> None:
    press = Press(1, PressKind.KIND, YtChannelKind.MEDIA)

    assert press.decision() is None
    assert press.decision(YtLang.RU) == Decision(
        ChannelStatus.SEED, kind=YtChannelKind.MEDIA, language=YtLang.RU
    )


def test_a_language_press_decides_the_seed_whole() -> None:
    press = Press(1, PressKind.LANG, (YtChannelKind.MEDIA, YtLang.EN))

    assert press.decision() == Decision(
        ChannelStatus.SEED, kind=YtChannelKind.MEDIA, language=YtLang.EN
    )


def test_a_reason_press_decides_and_maybe_is_one() -> None:
    assert Press(1, PressKind.REASON, YtRejectReason.CRYPTO).decision() == (
        Decision(ChannelStatus.REJECTED, reason=YtRejectReason.CRYPTO)
    )
    assert Press(1, PressKind.MAYBE).decision() == Decision(
        ChannelStatus.MAYBE
    )
    assert Press(1, PressKind.SEED).decision() is None


# --- the decision line ------------------------------------------------


def test_the_decision_lines() -> None:
    assert (
        decision_line(
            Decision(
                ChannelStatus.SEED,
                kind=YtChannelKind.MEDIA,
                language=YtLang.EN,
            )
        )
        == "✅ В сиды · медиа · EN"
    )
    assert (
        decision_line(
            Decision(
                ChannelStatus.SEED,
                kind=YtChannelKind.EDUCATION,
                language=YtLang.OTHER,
            )
        )
        == "✅ В сиды · обучение · Другой"
    )
    assert (
        decision_line(
            Decision(
                ChannelStatus.REJECTED, reason=YtRejectReason.CONSUMER_TECH
            )
        )
        == "🚫 Отклонён · гаджеты"
    )
    assert decision_line(Decision(ChannelStatus.MAYBE)) == MAYBE
