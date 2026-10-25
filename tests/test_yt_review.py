"""The review file: rendered for a text editor, read back strictly."""

from datetime import UTC, datetime, timedelta

import pytest
from ytfakes import channel_id

from itgraph.db.models import (
    ChannelStatus,
    YtChannelKind,
    YtLang,
    YtRejectReason,
)
from itgraph.yt_review import (
    COLUMNS,
    ExportRow,
    UnreadableReviewFile,
    parse_review,
    render_review,
)

EXPORTED = datetime(2026, 10, 26, 9, 12, tzinfo=UTC)
LATER = EXPORTED + timedelta(hours=2)


def _file(*rows: ExportRow) -> str:
    return render_review(rows, exported_at=EXPORTED)


def _edit(text: str, number: int, **values: str) -> str:
    """Fill in editable columns of the n-th channel row, as an editor would."""
    lines = text.splitlines()
    rows = [i for i, line in enumerate(lines) if line.startswith("UC")]
    index = rows[number]
    fields = lines[index].split("\t")
    for column, value in values.items():
        fields[COLUMNS.index(column)] = value
    lines[index] = "\t".join(fields)
    return "\n".join(lines) + "\n"


def test_the_header_says_what_may_be_written_and_when_it_expires() -> None:
    text = _file()

    assert "# exported: 2026-10-26T09:12:00Z" in text
    assert "# expires:  2026-11-25" in text
    assert "never commit it" in text
    assert "personal company media event education" in text
    assert "en ru other" in text
    assert "consumer_tech" in text and "other_scene" not in text
    assert 'src/itgraph/README.md, "YouTube: разметка"' in text
    assert "\t".join(COLUMNS) in text


def test_a_round_trip() -> None:
    text = _file(
        ExportRow(channel_id(1), title="Fake One"),
        ExportRow(channel_id(2), decision="maybe", kind="media", note="hm"),
        ExportRow(channel_id(3)),
    )
    text = _edit(text, 0, decision="seed", kind="personal", lang="en")
    text = _edit(
        text, 2, decision="reject", reason="consumer_tech", note="gadgets"
    )

    parsed = parse_review(text, now=LATER)

    assert parsed.problems == []
    assert parsed.exported_at == EXPORTED
    seed, maybe, reject = parsed.decisions
    assert (seed.channel_id, seed.status, seed.kind, seed.lang) == (
        channel_id(1),
        ChannelStatus.SEED,
        YtChannelKind.PERSONAL,
        YtLang.EN,
    )
    assert (maybe.status, maybe.kind, maybe.note) == (
        ChannelStatus.MAYBE,
        YtChannelKind.MEDIA,
        "hm",
    )
    assert (reject.status, reject.reason, reject.note) == (
        ChannelStatus.REJECTED,
        YtRejectReason.CONSUMER_TECH,
        "gadgets",
    )
    assert seed.where == "line 10"


def test_a_blank_row_is_skipped() -> None:
    parsed = parse_review(_file(ExportRow(channel_id(1))), now=LATER)

    assert parsed.decisions == []
    assert parsed.skipped == 1
    assert parsed.problems == []


def test_every_mistake_is_reported() -> None:
    text = _file(*(ExportRow(channel_id(n)) for n in range(6)))
    text = _edit(text, 0, decision="seed", lang="en")  # no kind
    text = _edit(text, 1, decision="seed", kind="personal")  # no lang
    text = _edit(text, 2, decision="reject")  # no reason
    text = _edit(text, 3, decision="seed", kind="persnal", lang="en")
    text = _edit(text, 4, kind="media")  # a decision forgotten
    text = _edit(
        text, 5, decision="seed", kind="media", lang="en", reason="ads"
    )
    text += f"{channel_id(0)}\tmaybe\n"  # listed twice

    problems = parse_review(text, now=LATER).problems

    said = [str(problem) for problem in problems]
    assert any("line 10" in line and "needs a kind" in line for line in said)
    assert any("line 11" in line and "needs a lang" in line for line in said)
    assert any("line 12" in line and "needs a reason" in line for line in said)
    assert any("line 13" in line and "'persnal'" in line for line in said)
    assert any("line 14" in line and "no decision" in line for line in said)
    assert any(
        "line 15" in line and "only to a rejection" in line for line in said
    )
    assert any("line 16" in line and "listed twice" in line for line in said)


def test_values_match_whatever_their_case() -> None:
    text = _edit(
        _file(ExportRow(channel_id(1))),
        0,
        decision="Seed",
        kind="EDUCATION",
        lang="Other",
    )

    [decision] = parse_review(text, now=LATER).decisions

    assert (decision.kind, decision.lang) == (
        YtChannelKind.EDUCATION,
        YtLang.OTHER,
    )


def test_a_tab_in_a_title_cannot_break_the_row() -> None:
    text = _file(ExportRow(channel_id(1), title="Fake\tTitle\nOn Two Lines"))

    assert "Fake Title On Two Lines" in text
    assert parse_review(text, now=LATER).problems == []


def test_a_row_with_too_many_fields_is_a_problem() -> None:
    text = _file(ExportRow(channel_id(1))).rstrip("\n") + "\textra\n"

    [problem] = parse_review(text, now=LATER).problems

    assert "a tab inside a value?" in str(problem)


def test_a_file_without_its_header_is_refused() -> None:
    text = "\n".join(
        line
        for line in _file(ExportRow(channel_id(1))).splitlines()
        if not line.startswith("# exported")
    )

    with pytest.raises(UnreadableReviewFile, match="export moment"):
        parse_review(text, now=LATER)


def test_an_expired_file_is_refused() -> None:
    text = _file(ExportRow(channel_id(1)))

    parse_review(text, now=EXPORTED + timedelta(days=30))  # the last day
    with pytest.raises(UnreadableReviewFile, match="Delete it"):
        parse_review(text, now=EXPORTED + timedelta(days=30, minutes=1))


def test_rearranged_columns_are_refused() -> None:
    text = _file(ExportRow(channel_id(1))).replace(
        "channel_id\tdecision", "decision\tchannel_id"
    )

    with pytest.raises(UnreadableReviewFile, match="column line"):
        parse_review(text, now=LATER)


def test_tabs_turned_into_spaces_are_named_as_such() -> None:
    """An editor converting tabs on save breaks the file without anyone
    touching a column; the refusal says so instead of "changed"."""
    text = _file(ExportRow(channel_id(1))).replace("\t", "    ")

    with pytest.raises(UnreadableReviewFile, match="spaces, not tabs"):
        parse_review(text, now=LATER)
