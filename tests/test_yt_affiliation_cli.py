"""The `itgraph yt affiliates` and `itgraph yt family` commands.

``test_cli.py``'s affiliation tests, carried over to the YouTube tables:
the list is ranked with its evidence on screen, a re-run never touches
a decision, and the family commands record what the review decided. What
is YouTube's own here is the corroborated-group block — one decision for
a whole token's carriers, bounded honestly under ``--limit`` — and the
token rejection that prices a noisy block at one review.
"""

import asyncio
from collections.abc import Iterator
from datetime import UTC, datetime
from typing import Any

import httpx
import pytest
from sqlalchemy import text
from typer.testing import CliRunner
from ytfakes import channel_id, channel_resource

from itgraph.cli import app
from itgraph.db import session as db_session
from itgraph.db.models import ChannelStatus, YtChannel, YtChannelKind, YtLang
from itgraph.db.session import Database
from itgraph.db.yt_channels import create_channels, store_payloads

runner = CliRunner()

A = channel_id(1)
B = channel_id(2)
C = channel_id(3)
D = channel_id(4)
E = channel_id(5)


def use_test_database(monkeypatch: pytest.MonkeyPatch, url: str) -> None:
    """Point every command at the throwaway database.

    The same reason as every other CLI test: the command builds its own
    ``Database`` inside its own event loop.
    """
    original = db_session.Database
    monkeypatch.setattr(db_session, "Database", lambda: original(url))


@pytest.fixture
def offline(monkeypatch: pytest.MonkeyPatch) -> Iterator[None]:
    """Any attempt to build an HTTP client fails the command."""

    def refuse(*args: Any, **kwargs: Any) -> None:
        raise AssertionError("an offline command built an HTTP client")

    monkeypatch.setattr(httpx.AsyncClient, "__init__", refuse)
    yield


def seed_affiliation_fixture(url: str) -> None:
    """A corroborated group of three, two unconnected channels.

    ``A``'s description names ``B`` by id link and all three titles carry
    ``acme``, so the group signal proposes every pair among them; the
    reference also fires on the pair it named. ``D`` and ``E`` share no
    token with anything, so the list holds exactly the group.
    """

    async def build() -> None:
        database = Database(url)
        try:
            async with database.session() as session:
                await create_channels(session, [A, B, C, D, E])
                payloads: dict[str, dict[str, Any]] = {
                    A: channel_resource(1, handle="acmemain"),
                    B: channel_resource(2, handle="acmeblog"),
                    C: channel_resource(3, handle="acmeextra"),
                    D: channel_resource(4, handle="deltadiary"),
                    E: channel_resource(5, handle="echoessays"),
                }
                for channel, title in (
                    (A, "Acme Show"),
                    (B, "Acme Blog"),
                    (C, "Acme Extra"),
                    (D, "Delta Diary"),
                    (E, "Echo Essays"),
                ):
                    payloads[channel]["snippet"]["title"] = title
                payloads[A]["snippet"]["description"] = (
                    f"We also run youtube.com/channel/{B}"
                )
                await store_payloads(
                    session, payloads, fetched_at=datetime.now(UTC)
                )
                for channel in (A, B, C, D, E):
                    record = await session.get(YtChannel, channel)
                    assert record is not None
                    record.status = ChannelStatus.SEED
                    record.kind = YtChannelKind.PERSONAL
                    record.lang = YtLang.EN
        finally:
            await database.dispose()

    asyncio.run(build())


def seed_undescribed_fixture(url: str) -> None:
    """Three seed channels with payloads that carry no description.

    The shape a fresh inventory has before the first `yt refresh`
    completes: titles to read, nothing for the two description-reading
    signals to look at.
    """

    async def build() -> None:
        database = Database(url)
        try:
            async with database.session() as session:
                await create_channels(session, [A, B, C])
                payloads: dict[str, dict[str, Any]] = {}
                for channel, number, handle, title in (
                    (A, 1, "kindredshow", "Kindred Show"),
                    (B, 2, "kindredblog", "Kindred Blog"),
                    (C, 3, "kindredextra", "Kindred Extra"),
                ):
                    payload = channel_resource(number, handle=handle)
                    payload["snippet"]["title"] = title
                    # Absent, not empty: a channel YouTube has no
                    # description for says so by omitting the field.
                    del payload["snippet"]["description"]
                    payloads[channel] = payload
                await store_payloads(
                    session, payloads, fetched_at=datetime.now(UTC)
                )
                for channel in (A, B, C):
                    record = await session.get(YtChannel, channel)
                    assert record is not None
                    record.status = ChannelStatus.SEED
                    record.kind = YtChannelKind.PERSONAL
                    record.lang = YtLang.EN
        finally:
            await database.dispose()

    asyncio.run(build())


def scalar(url: str, statement: str) -> Any:
    """One value out of the throwaway database, for what was written."""

    async def read() -> Any:
        database = Database(url)
        try:
            async with database.session() as session:
                return await session.scalar(text(statement))
        finally:
            await database.dispose()

    return asyncio.run(read())


# --- the list -----------------------------------------------------------


def test_affiliates_ranks_pairs_and_shows_its_evidence(
    monkeypatch: pytest.MonkeyPatch, database_url: str, offline: None
) -> None:
    use_test_database(monkeypatch, database_url)
    seed_affiliation_fixture(database_url)

    result = runner.invoke(app, ["yt", "affiliates"])

    assert result.exit_code == 0, result.output
    assert "candidate pairs proposed" in result.output
    # The evidence is on screen, not folded into the score.
    assert "group:acme/3" in result.output
    assert "ref:a_to_b" in result.output
    # The block carries the confirmation, ready to run.
    assert "token acme — 3 channels, 3 pairs" in result.output
    assert "itgraph yt family acmemain acmeblog acmeextra" in result.output


def test_affiliates_shows_a_multi_signal_pair_once_with_all_evidence(
    monkeypatch: pytest.MonkeyPatch, database_url: str, offline: None
) -> None:
    """The pair the reference itself named carries two facts, and both
    are named — once, inside its group."""
    use_test_database(monkeypatch, database_url)
    seed_affiliation_fixture(database_url)

    result = runner.invoke(app, ["yt", "affiliates"])

    lines = [
        line
        for line in result.output.splitlines()
        if "@acmemain" in line and "@acmeblog" in line
    ]
    assert len(lines) == 1
    assert "group:acme/3" in lines[0]
    assert "ref:a_to_b" in lines[0]


def test_affiliates_reports_coverage(
    monkeypatch: pytest.MonkeyPatch, database_url: str, offline: None
) -> None:
    use_test_database(monkeypatch, database_url)
    seed_affiliation_fixture(database_url)

    result = runner.invoke(app, ["yt", "affiliates"])

    assert "payloads: 5 of 5 channels have one, 0 do not" in result.output
    assert "descriptions: 5 of 5 channels have one" in result.output


def test_affiliates_group_weight_reaches_the_score(
    monkeypatch: pytest.MonkeyPatch, database_url: str, offline: None
) -> None:
    """Settable per run, as the spec asks: the group's weight doubles what
    every pair scores for it, the ones it alone proposes included."""
    use_test_database(monkeypatch, database_url)
    seed_affiliation_fixture(database_url)

    default = runner.invoke(app, ["yt", "affiliates"])
    heavier = runner.invoke(app, ["yt", "affiliates", "--weight-group", "2.0"])

    assert heavier.exit_code == 0, heavier.output
    assert " 1.000  " in default.output
    assert " 2.500  " not in default.output
    assert " 2.000  " in heavier.output
    assert " 2.500  " in heavier.output


def test_affiliates_says_when_a_signal_could_not_run(
    monkeypatch: pytest.MonkeyPatch, database_url: str, offline: None
) -> None:
    """No descriptions anywhere: the two signals that read them have
    looked nowhere, and the report must not read as their having found
    nothing — the title token signal still has work to show."""
    use_test_database(monkeypatch, database_url)
    seed_undescribed_fixture(database_url)

    result = runner.invoke(app, ["yt", "affiliates"])

    assert result.exit_code == 0, result.output
    assert "payloads: 3 of 3 channels have one, 0 do not" in result.output
    assert "descriptions: 0 of 3 channels have one" in result.output
    assert "produced nothing for lack of data" in result.output
    assert "`itgraph yt refresh`" in result.output
    # What could run, did: the run found something, so the lack-of-data
    # line cannot be read as an empty result.
    assert "3 candidate pairs proposed over 3 channels" in result.output
    assert "token:kindred/3" in result.output


def test_affiliates_writes_no_family_link(
    monkeypatch: pytest.MonkeyPatch, database_url: str, offline: None
) -> None:
    """The one thing detection may never do, whatever the scores."""
    use_test_database(monkeypatch, database_url)
    seed_affiliation_fixture(database_url)

    result = runner.invoke(app, ["yt", "affiliates"])
    assert result.exit_code == 0, result.output

    assert (
        scalar(database_url, "SELECT count(*) FROM yt_channel_families") == 0
    )


def test_affiliates_makes_no_request(
    monkeypatch: pytest.MonkeyPatch, database_url: str, offline: None
) -> None:
    """Every input was collected by an earlier refresh, so this spends
    no quota and must not even build a client."""
    use_test_database(monkeypatch, database_url)
    seed_affiliation_fixture(database_url)

    result = runner.invoke(app, ["yt", "affiliates"])

    assert result.exit_code == 0, result.output


def test_affiliates_documents_its_defaults(
    monkeypatch: pytest.MonkeyPatch, database_url: str
) -> None:
    use_test_database(monkeypatch, database_url)

    result = runner.invoke(app, ["yt", "affiliates", "--help"])

    assert result.exit_code == 0, result.output
    assert "[default: 4]" in result.output  # min token length
    assert "[default: 3]" in result.output  # max token channels
    assert "[default: 50]" in result.output  # max corroborated channels
    assert "[default: 1.0]" in result.output  # reference and group weights
    assert "[default: 0.6]" in result.output  # token weight


def test_affiliates_refuses_a_parameter_and_writes_nothing(
    monkeypatch: pytest.MonkeyPatch, database_url: str, offline: None
) -> None:
    use_test_database(monkeypatch, database_url)
    seed_affiliation_fixture(database_url)

    result = runner.invoke(
        app, ["yt", "affiliates", "--max-token-channels", "1"]
    )

    assert result.exit_code != 0
    assert "max_token_channels" in result.output
    assert (
        scalar(database_url, "SELECT count(*) FROM yt_affiliation_candidates")
        == 0
    )
    assert (
        scalar(database_url, "SELECT count(*) FROM yt_affiliation_runs") == 0
    )


def test_affiliates_states_what_awaits_review(
    monkeypatch: pytest.MonkeyPatch, database_url: str, offline: None
) -> None:
    """Both counts, because they diverge for two different reasons."""
    use_test_database(monkeypatch, database_url)
    seed_affiliation_fixture(database_url)

    runner.invoke(app, ["yt", "family", A, B, "--reject"])
    result = runner.invoke(app, ["yt", "affiliates"])

    assert result.exit_code == 0, result.output
    # Proposed still counts the rejected pair — it is computed, stored and
    # kept inspectable — while the review list hides it.
    assert "3 candidate pairs proposed over 5 channels" in result.output
    assert "2 awaiting review" in result.output


def test_affiliates_hides_a_pair_with_no_seed_in_it(
    monkeypatch: pytest.MonkeyPatch, database_url: str, offline: None
) -> None:
    use_test_database(monkeypatch, database_url)
    seed_affiliation_fixture(database_url)

    async def kindred_and_unreviewed() -> None:
        """Give D and E a shared token to pair on, then take both out of
        scope: the pair is computed and stored either way, and hidden
        only from the reading."""
        database = Database(database_url)
        try:
            async with database.session() as session:
                await store_payloads(
                    session,
                    {
                        D: channel_resource(
                            4, handle="deltadiary", title="Kindred Diary"
                        ),
                        E: channel_resource(
                            5, handle="echoessays", title="Kindred Essays"
                        ),
                    },
                    fetched_at=datetime.now(UTC),
                )
                for channel in (D, E):
                    record = await session.get(YtChannel, channel)
                    assert record is not None
                    record.status = ChannelStatus.CANDIDATE
                    record.kind = None
                    record.lang = None
        finally:
            await database.dispose()

    asyncio.run(kindred_and_unreviewed())

    hidden = runner.invoke(app, ["yt", "affiliates"])
    shown = runner.invoke(app, ["yt", "affiliates", "--any-status"])

    assert hidden.exit_code == 0, hidden.output
    assert "@deltadiary" not in hidden.output
    # Computed and stored either way — only the reading is narrowed.
    assert "4 candidate pairs proposed over 5 channels" in hidden.output
    assert "3 awaiting review" in hidden.output
    assert "@deltadiary" in shown.output


# --- the block under a bound --------------------------------------------


def test_a_truncated_block_says_what_is_not_shown(
    monkeypatch: pytest.MonkeyPatch, database_url: str, offline: None
) -> None:
    use_test_database(monkeypatch, database_url)
    seed_affiliation_fixture(database_url)

    result = runner.invoke(app, ["yt", "affiliates", "--limit", "2"])

    assert result.exit_code == 0, result.output
    # The bound holds over pairs; the block it cut says so, and still
    # names every channel a confirmation would have to mention.
    assert "token acme — 3 channels, 3 pairs, 1 not shown" in result.output
    assert "itgraph yt family acmemain acmeblog acmeextra" in result.output


def test_a_partly_decided_group_shows_the_rest(
    monkeypatch: pytest.MonkeyPatch, database_url: str, offline: None
) -> None:
    use_test_database(monkeypatch, database_url)
    seed_affiliation_fixture(database_url)
    runner.invoke(app, ["yt", "affiliates"])

    confirmed = runner.invoke(app, ["yt", "family", B, C])
    result = runner.invoke(app, ["yt", "affiliates"])

    assert confirmed.exit_code == 0, confirmed.output
    assert result.exit_code == 0, result.output
    assert "token acme — 3 channels, 2 pairs" in result.output
    # The decided pair left the review list; the rest stayed a group.
    assert "@acmeblog" in result.output
    assert "2 candidate pairs proposed over 5 channels" in result.output


def test_the_confirm_line_wraps_as_a_shell_reads_it(
    monkeypatch: pytest.MonkeyPatch, database_url: str, offline: None
) -> None:
    """A block of real size names more channels than one line holds."""
    use_test_database(monkeypatch, database_url)

    async def build() -> None:
        database = Database(database_url)
        try:
            async with database.session() as session:
                ids = [channel_id(10 + n) for n in range(12)]
                await create_channels(session, ids)
                payloads: dict[str, dict[str, Any]] = {}
                for n, channel in enumerate(ids, 10):
                    payload = channel_resource(n, handle=f"acmebranch{n}")
                    # Digits are too short to be tokens, so the one token
                    # the titles share is the one under test.
                    payload["snippet"]["title"] = f"Acme {n}"
                    payloads[channel] = payload
                payloads[ids[0]]["snippet"]["description"] = (
                    f"We also run youtube.com/channel/{ids[1]}"
                )
                await store_payloads(
                    session, payloads, fetched_at=datetime.now(UTC)
                )
                for channel in ids:
                    record = await session.get(YtChannel, channel)
                    assert record is not None
                    record.status = ChannelStatus.SEED
                    record.kind = YtChannelKind.PERSONAL
                    record.lang = YtLang.EN
        finally:
            await database.dispose()

    asyncio.run(build())

    result = runner.invoke(app, ["yt", "affiliates"])

    assert result.exit_code == 0, result.output
    assert "token acme — 12 channels, 66 pairs" in result.output
    lines = result.output.splitlines()
    start = next(
        index
        for index, line in enumerate(lines)
        if line.startswith("  itgraph yt family")
    )
    confirm = []
    for line in lines[start:]:
        confirm.append(line)
        if not line.endswith("\\"):
            break
    assert len(confirm) > 1
    # Every line but the last carries the shell's continuation, so the
    # wrapped command is one command.
    assert all(line.endswith("\\") for line in confirm[:-1])
    assert not confirm[-1].endswith("\\")


# --- the decisions ------------------------------------------------------


def test_family_confirms_and_the_run_leaves_it_alone(
    monkeypatch: pytest.MonkeyPatch, database_url: str, offline: None
) -> None:
    use_test_database(monkeypatch, database_url)
    seed_affiliation_fixture(database_url)

    confirmed = runner.invoke(app, ["yt", "family", "@acmemain", "@acmeblog"])
    after = runner.invoke(app, ["yt", "affiliates"])
    everything = runner.invoke(app, ["yt", "affiliates", "--all"])

    assert confirmed.exit_code == 0, confirmed.output
    assert "1 pairs recorded; family of 2 channels" in confirmed.output
    # The confirmed pair is not proposed again, and stays inspectable.
    assert "2 candidate pairs proposed over 5 channels" in after.output
    assert "@acmemain" in after.output
    assert "@acmemain" in everything.output


def test_family_rejects_and_the_pair_leaves_the_review_list(
    monkeypatch: pytest.MonkeyPatch, database_url: str, offline: None
) -> None:
    use_test_database(monkeypatch, database_url)
    seed_affiliation_fixture(database_url)

    runner.invoke(app, ["yt", "affiliates"])
    rejected = runner.invoke(
        app,
        [
            "yt",
            "family",
            A,
            B,
            "--reject",
            "--note",
            "different owners",
        ],
    )
    pending = runner.invoke(app, ["yt", "affiliates"])
    everything = runner.invoke(app, ["yt", "affiliates", "--all"])

    assert rejected.exit_code == 0, rejected.output
    assert "not affiliated" in rejected.output
    assert "2 awaiting review" in pending.output
    # Kept, not deleted: the rejection is what stops it being proposed.
    assert (
        scalar(
            database_url,
            "SELECT count(*) FROM yt_affiliation_candidates "
            "WHERE decision = 'rejected'",
        )
        == 1
    )
    assert "@acmemain" in everything.output


def test_family_rejects_a_whole_token(
    monkeypatch: pytest.MonkeyPatch, database_url: str, offline: None
) -> None:
    use_test_database(monkeypatch, database_url)
    seed_affiliation_fixture(database_url)
    runner.invoke(app, ["yt", "affiliates"])
    runner.invoke(app, ["yt", "family", B, C])

    rejected = runner.invoke(
        app, ["yt", "family", "--reject-token", "acme", "--note", "a subject"]
    )
    after = runner.invoke(app, ["yt", "affiliates"])

    assert rejected.exit_code == 0, rejected.output
    assert "2 pairs rejected over token 'acme'" in rejected.output
    assert "0 awaiting review" in after.output
    # The confirmed pair is untouched by the token's rejection.
    assert (
        scalar(
            database_url,
            "SELECT count(*) FROM yt_affiliation_candidates "
            "WHERE decision = 'confirmed'",
        )
        == 1
    )
    assert (
        scalar(
            database_url,
            "SELECT decision_note FROM yt_affiliation_candidates "
            "WHERE decision = 'rejected'",
        )
        == "a subject"
    )


def test_a_token_rejection_without_a_note_names_the_token(
    monkeypatch: pytest.MonkeyPatch, database_url: str, offline: None
) -> None:
    use_test_database(monkeypatch, database_url)
    seed_affiliation_fixture(database_url)
    runner.invoke(app, ["yt", "affiliates"])

    rejected = runner.invoke(app, ["yt", "family", "--reject-token", "acme"])

    assert rejected.exit_code == 0, rejected.output
    assert (
        scalar(
            database_url,
            "SELECT decision_note FROM yt_affiliation_candidates "
            "WHERE decision = 'rejected'",
        )
        == "token 'acme'"
    )


def test_family_reject_all_clears_the_review_list(
    monkeypatch: pytest.MonkeyPatch, database_url: str, offline: None
) -> None:
    use_test_database(monkeypatch, database_url)
    seed_affiliation_fixture(database_url)
    runner.invoke(app, ["yt", "affiliates"])
    # One pair decided beforehand: a bulk rejection never rewrites a
    # decision, in either direction.
    runner.invoke(app, ["yt", "family", B, C])

    result = runner.invoke(
        app, ["yt", "family", "--reject-all"], input="yes\n"
    )
    after = runner.invoke(app, ["yt", "affiliates"])

    assert result.exit_code == 0, result.output
    assert "2 pairs await review" in result.output
    assert "Type yes to continue:" in result.output
    assert "2 pairs rejected" in result.output
    assert "0 awaiting review" in after.output
    assert (
        scalar(
            database_url,
            "SELECT count(*) FROM yt_affiliation_candidates "
            "WHERE decision = 'confirmed'",
        )
        == 1
    )
    assert (
        scalar(
            database_url,
            "SELECT decision_note FROM yt_affiliation_candidates "
            "WHERE decision = 'rejected'",
        )
        == "--reject-all"
    )


def test_family_reject_all_declined_writes_nothing(
    monkeypatch: pytest.MonkeyPatch, database_url: str, offline: None
) -> None:
    use_test_database(monkeypatch, database_url)
    seed_affiliation_fixture(database_url)
    runner.invoke(app, ["yt", "affiliates"])

    declined = runner.invoke(
        app, ["yt", "family", "--reject-all"], input="no\n"
    )
    # Empty stdin is EOF: a non-interactive context declines rather than
    # hangs.
    eof = runner.invoke(app, ["yt", "family", "--reject-all"], input="")

    assert declined.exit_code == 1
    assert "3 pairs await review" in declined.output
    assert "nothing written" in declined.output
    assert eof.exit_code == 1
    assert "nothing written" in eof.output
    assert (
        scalar(
            database_url,
            "SELECT count(*) FROM yt_affiliation_candidates "
            "WHERE decision = 'rejected'",
        )
        == 0
    )


def test_family_reject_all_with_nothing_pending_prompts_nothing(
    monkeypatch: pytest.MonkeyPatch, database_url: str
) -> None:
    use_test_database(monkeypatch, database_url)
    seed_affiliation_fixture(database_url)

    result = runner.invoke(app, ["yt", "family", "--reject-all"])

    assert result.exit_code == 0, result.output
    assert "nothing awaits review" in result.output
    assert "Type yes" not in result.output


def test_family_reject_all_refuses_other_decisions_and_channels(
    monkeypatch: pytest.MonkeyPatch, database_url: str
) -> None:
    use_test_database(monkeypatch, database_url)
    seed_affiliation_fixture(database_url)

    with_token = runner.invoke(
        app, ["yt", "family", "--reject-all", "--reject-token", "acme"]
    )
    with_channels = runner.invoke(app, ["yt", "family", A, "--reject-all"])
    and_reject = runner.invoke(
        app, ["yt", "family", A, B, "--reject", "--reject-all"]
    )

    assert with_token.exit_code != 0
    assert "at most one" in with_token.output
    assert with_channels.exit_code != 0
    assert "--reject-all takes no channels" in with_channels.output
    assert and_reject.exit_code != 0


def test_family_withdraws_a_confirmation(
    monkeypatch: pytest.MonkeyPatch, database_url: str, offline: None
) -> None:
    use_test_database(monkeypatch, database_url)
    seed_affiliation_fixture(database_url)

    runner.invoke(app, ["yt", "family", A, B])
    result = runner.invoke(app, ["yt", "family", A, B, "--withdraw"])
    after = runner.invoke(app, ["yt", "affiliates"])

    assert result.exit_code == 0, result.output
    assert "decision withdrawn" in result.output
    # The pair returns to being reviewable.
    assert "3 candidate pairs proposed" in after.output
    assert "@acmemain" in after.output


def test_family_finds_channels_by_handle_and_id(
    monkeypatch: pytest.MonkeyPatch, database_url: str, offline: None
) -> None:
    use_test_database(monkeypatch, database_url)
    seed_affiliation_fixture(database_url)

    result = runner.invoke(app, ["yt", "family", "@acmeextra", A])

    assert result.exit_code == 0, result.output
    assert (
        scalar(
            database_url,
            "SELECT count(*) FROM yt_affiliation_candidates "
            "WHERE decision = 'confirmed'",
        )
        == 1
    )


def test_family_refuses_shapes_that_name_no_pair(
    monkeypatch: pytest.MonkeyPatch, database_url: str
) -> None:
    use_test_database(monkeypatch, database_url)

    one = runner.invoke(app, ["yt", "family", A])
    three = runner.invoke(app, ["yt", "family", A, B, C, "--reject"])
    both_flags = runner.invoke(
        app, ["yt", "family", A, B, "--reject", "--withdraw"]
    )
    token_and_channels = runner.invoke(
        app, ["yt", "family", A, "--reject-token", "acme"]
    )

    assert one.exit_code != 0
    assert "at least two" in one.output
    assert three.exit_code != 0
    assert "exactly two" in three.output
    assert both_flags.exit_code != 0
    assert token_and_channels.exit_code != 0
    assert "takes no channels" in token_and_channels.output


def test_family_fails_on_an_unknown_channel_writing_nothing(
    monkeypatch: pytest.MonkeyPatch, database_url: str, offline: None
) -> None:
    use_test_database(monkeypatch, database_url)
    seed_affiliation_fixture(database_url)

    result = runner.invoke(app, ["yt", "family", A, channel_id(99)])

    assert result.exit_code != 0
    assert "no YouTube channel" in result.output
    assert (
        scalar(database_url, "SELECT count(*) FROM yt_affiliation_candidates")
        == 0
    )
