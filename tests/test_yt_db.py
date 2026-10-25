"""The YouTube tables' own rules, as the database enforces them."""

from typing import Any

import pytest
from sqlalchemy import text
from sqlalchemy.exc import DBAPIError, IntegrityError
from ytfakes import channel_id

from itgraph.db.session import Database


async def _insert(database: Database, **columns: Any) -> None:
    row = {
        "channel_id": channel_id(1),
        "status": "candidate",
        "kind": None,
        "lang": None,
        "reject_reason": None,
        "discovered_via": "manual",
        **columns,
    }
    async with database.session() as session:
        await session.execute(
            text(
                "INSERT INTO yt_channels "
                "(channel_id, status, kind, lang, reject_reason, "
                "discovered_via) "
                "VALUES (:channel_id, CAST(:status AS channel_status), "
                "CAST(:kind AS yt_channel_kind), CAST(:lang AS yt_lang), "
                "CAST(:reject_reason AS yt_reject_reason), "
                "CAST(:discovered_via AS yt_discovery_source))"
            ),
            row,
        )


async def test_a_new_record_is_accepted(database: Database) -> None:
    await _insert(database)


async def test_a_rejection_without_a_reason_is_refused(
    database: Database,
) -> None:
    with pytest.raises(IntegrityError):
        await _insert(database, status="rejected")


async def test_a_reason_without_a_rejection_is_refused(
    database: Database,
) -> None:
    with pytest.raises(IntegrityError):
        await _insert(database, reject_reason="not_it")


async def test_a_seed_without_a_kind_is_refused(database: Database) -> None:
    with pytest.raises(IntegrityError):
        await _insert(database, status="seed", lang="en")


async def test_a_seed_without_a_language_is_refused(
    database: Database,
) -> None:
    with pytest.raises(IntegrityError):
        await _insert(database, status="seed", kind="personal")


async def test_a_labelled_seed_is_accepted(database: Database) -> None:
    await _insert(database, status="seed", kind="education", lang="other")


@pytest.mark.parametrize(
    "malformed",
    [
        "UC123",
        "@fakehandle",
        "fakehandle",
        # Right length, wrong prefix: a playlist id rather than a channel.
        "UUfake_channel_000000001",
        # One character too many.
        "UCfake_channel_0000000001",
    ],
)
async def test_a_malformed_channel_id_is_refused(
    database: Database, malformed: str
) -> None:
    with pytest.raises(IntegrityError):
        await _insert(database, channel_id=malformed)


async def test_the_status_is_telegrams_own_type(database: Database) -> None:
    """Shared on purpose: one workflow, one set of values."""
    async with database.session() as session:
        types = await session.execute(
            text(
                "SELECT table_name, udt_name FROM information_schema.columns "
                "WHERE column_name = 'status' "
                "AND table_name IN ('channels', 'yt_channels') "
                "ORDER BY table_name"
            )
        )
        assert types.all() == [
            ("channels", "channel_status"),
            ("yt_channels", "channel_status"),
        ]


async def test_youtube_values_are_not_telegrams(database: Database) -> None:
    """`education` is a YouTube kind and nothing a Telegram row may hold."""
    async with database.session() as session:
        with pytest.raises(DBAPIError, match="channel_kind"):
            await session.execute(
                text("SELECT CAST('education' AS channel_kind)")
            )
