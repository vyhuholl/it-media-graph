"""What a restored copy may hold, on seeded rows of every shape.

`neutralise.sql` and `readback.sql` run here as the restore runs them, as whole
scripts over one connection. An empty table proves nothing, so every statement
has rows to act on, and each removed in turn must fail the read-back.

People in payloads are cleared by the schema's revision, which a restore runs
before neutralising; `clear_people` stands in for it here, with that revision's
own statements. The read-back looks for people by type, so a person at a path no
statement ever named fails it too.
"""

import json
import re
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

import asyncpg
import pytest
from people_cases import ALL, CASES, ONE_OF_EACH, Case, newest_rule
from roleharness import ANSIBLE_DIR
from sqlalchemy.engine import make_url

from itgraph.db.backup import EXCLUDED_DATA
from itgraph.db.models import Base
from itgraph.db.session import Database

FILES = ANSIBLE_DIR / "itgraph" / "files"
NEUTRALISE = (FILES / "neutralise.sql").read_text()
READBACK = (FILES / "readback.sql").read_text()

NOW = datetime(2026, 11, 6, 9, 0, tzinfo=UTC)
SEED_FROM_SUBSCRIPTIONS = 1
REJECTED_FROM_SUBSCRIPTIONS = 2
CANDIDATE_FROM_SUBSCRIPTIONS = 3
MANUAL_SEED = 4
FAKE_USER = 777000111
FAKE_YT = "UCfake" + "0" * 18


RULE = newest_rule()


def statements() -> list[str]:
    """The script's statements, without comments or the transaction's ends."""
    body = re.sub(r"^--.*$", "", NEUTRALISE, flags=re.MULTILINE)
    parts = [part.strip() for part in body.split(";")]
    return [p for p in parts if p and p not in ("BEGIN", "COMMIT")]


async def connect(database_url: str) -> Any:
    url = make_url(database_url)
    return await asyncpg.connect(
        host=url.host,
        port=url.port,
        user=url.username,
        password=url.password,
        database=url.database,
    )


async def seed(connection: Any) -> None:
    await connection.execute(
        """
        INSERT INTO channels (tg_id, username, status, discovered_via, kind,
                              kind_note, reject_reason, reject_note)
        VALUES
          (1, 'fake_seed_mine', 'seed', 'own_subscriptions', 'personal',
           'a fake note', NULL, NULL),
          (2, 'fake_rejected_mine', 'rejected', 'own_subscriptions', NULL,
           NULL, 'not_it', 'a fake reason'),
          (3, 'fake_candidate_mine', 'candidate', 'own_subscriptions', NULL,
           NULL, NULL, NULL),
          (4, 'fake_seed_other', 'seed', 'manual', 'media',
           NULL, NULL, NULL),
          (5, 'fake_rejected_other', 'rejected', 'forward', NULL,
           NULL, 'not_it', 'a fake reason')
        """
    )
    await connection.execute(
        f"""
        INSERT INTO yt_channels (channel_id, status, reject_reason, reject_note,
                                 kind_note, discovered_via)
        VALUES ('{FAKE_YT}', 'rejected', 'not_it', 'a fake reason',
                'a fake kind note', 'manual')
        """
    )
    await connection.execute(
        """
        INSERT INTO affiliation_candidates (channel_a, channel_b, score,
                                            decision_note)
        VALUES (4, 5, 0.5, 'a fake note on a pair')
        """
    )
    await connection.execute(
        "INSERT INTO pending_mentions (username) VALUES ('fake_person')"
    )
    payloads: list[dict[str, Any]] = [
        {
            "id": 10,
            "_": "Message",
            "message": "a public post",
            "views": 123,
            "replies": {
                "replies": 4,
                "recent_repliers": [
                    {"_": "PeerUser", "user_id": FAKE_USER},
                ],
            },
            "reactions": {
                "results": [],
                "recent_reactions": [
                    {"peer_id": {"_": "PeerUser", "user_id": FAKE_USER}},
                ],
            },
            "post_author": "A Fake Author",
            "from_id": {"_": "PeerUser", "user_id": FAKE_USER},
            "entities": [
                {
                    "_": "MessageEntityMentionName",
                    "offset": 0,
                    "length": 1,
                    "user_id": FAKE_USER,
                },
                {"_": "MessageEntityUrl", "offset": 2, "length": 3},
            ],
            "fwd_from": {
                "from_id": {"_": "PeerUser", "user_id": FAKE_USER},
                "from_name": "A Fake Sender",
                "post_author": "Another Fake Author",
            },
        },
        {
            "id": 11,
            "_": "Message",
            "message": "a forward",
            "from_id": {"_": "PeerChannel", "channel_id": 4},
            "entities": [
                {
                    "_": "InputMessageEntityMentionName",
                    "offset": 0,
                    "length": 1,
                    "user_id": {"_": "InputUser", "user_id": FAKE_USER},
                }
            ],
            "fwd_from": {
                "from_id": {"_": "PeerChannel", "channel_id": 4},
                "channel_post": 99,
            },
        },
    ]
    for payload in payloads:
        await connection.execute(
            "INSERT INTO raw_messages (channel_id, msg_id, payload) "
            "VALUES (4, $1, $2::jsonb)",
            payload["id"],
            json.dumps(payload),
        )
    await connection.execute(
        "INSERT INTO message_metrics (channel_id, msg_id, observed_at, views) "
        "VALUES (4, 10, $1, 123)",
        NOW,
    )
    await connection.execute(
        """
        INSERT INTO alerts (kind, channel_id, msg_id, band, value, raised_at)
        VALUES ('views_spike', 4, 10, 1, 4.2, $1)
        """,
        NOW - timedelta(hours=1),
    )


async def clear_people(connection: Any) -> None:
    """What the schema's revision does in a restore, before this file runs."""
    for statement in RULE.FUNCTIONS:
        await connection.execute(statement)
    for table in RULE.TABLES:
        await connection.execute(
            f"UPDATE {table} SET payload = pg_temp.itgraph_without_people(payload) "
            f"WHERE jsonb_path_exists(payload, '{RULE.NAMES_SOMEONE}')"
        )


async def payload_of(connection: Any, msg_id: int) -> dict[str, Any]:
    raw = await connection.fetchval(
        "SELECT payload FROM raw_messages WHERE msg_id = $1", msg_id
    )
    loaded: dict[str, Any] = json.loads(raw)
    return loaded


async def test_a_seeded_database_fails_the_read_back(
    database: Database, database_url: str
) -> None:
    connection = await connect(database_url)
    try:
        await seed(connection)
        with pytest.raises(asyncpg.RaiseError) as caught:
            await connection.execute(READBACK)
    finally:
        await connection.close()

    message = str(caught.value)
    for named in (
        "channels from the operator's subscriptions",
        "notes on channels",
        "notes on YouTube channels",
        "notes on affiliation decisions",
        "pending mentions",
        "undelivered alerts",
        "users in posts: 2",
        "mentions by id in posts: 2",
        "names in posts: 1",
    ):
        assert named in message, named


async def test_neutralising_passes_the_read_back_and_keeps_what_it_should(
    database: Database, database_url: str
) -> None:
    connection = await connect(database_url)
    try:
        await seed(connection)
        await clear_people(connection)
        await connection.execute(NEUTRALISE)
        await connection.execute(READBACK)

        channels = {
            row["tg_id"]: row
            for row in await connection.fetch(
                "SELECT tg_id, status::text, discovered_via::text, kind::text, "
                "kind_note FROM channels"
            )
        }
        # The seed stays, as another source; what was only read is gone.
        assert set(channels) == {SEED_FROM_SUBSCRIPTIONS, MANUAL_SEED, 5}
        assert channels[1]["discovered_via"] == "manual"
        assert channels[1]["kind"] == "personal"
        assert channels[1]["kind_note"] is None
        # The decision stays, without what was written beside it.
        assert (
            await connection.fetchval(
                "SELECT decision_note FROM affiliation_candidates"
            )
            is None
        )

        first = await payload_of(connection, 10)
        # A text, a counter and a non-person entity are what the copy is for.
        assert first["message"] == "a public post"
        assert first["views"] == 123
        assert first["replies"]["replies"] == 4
        assert [e["_"] for e in first["entities"]] == ["MessageEntityUrl"]
        text = json.dumps(first)
        assert str(FAKE_USER) not in text
        assert "A Fake" not in text
        assert "Another Fake" not in text

        second = await payload_of(connection, 11)
        # A forward from a channel is the graph itself.
        assert second["fwd_from"]["from_id"]["channel_id"] == 4
        assert second["from_id"]["channel_id"] == 4
        assert second["entities"] == []
        assert str(FAKE_USER) not in json.dumps(second)

        assert (
            await connection.fetchval("SELECT count(*) FROM message_metrics")
            == 1
        )
        assert (
            await connection.fetchval(
                "SELECT count(*) FROM alerts WHERE delivered_at IS NULL"
            )
            == 0
        )
    finally:
        await connection.close()


async def test_neutralising_twice_changes_nothing_more(
    database: Database, database_url: str
) -> None:
    connection = await connect(database_url)
    try:
        await seed(connection)
        await clear_people(connection)
        await connection.execute(NEUTRALISE)
        before = await payload_of(connection, 10)
        await connection.execute(NEUTRALISE)
        await connection.execute(READBACK)
        assert await payload_of(connection, 10) == before
    finally:
        await connection.close()


@pytest.mark.parametrize("left_out", range(len(statements())))
async def test_each_statement_is_needed(
    database: Database, database_url: str, left_out: int
) -> None:
    kept = [s for i, s in enumerate(statements()) if i != left_out]
    connection = await connect(database_url)
    try:
        await seed(connection)
        await clear_people(connection)
        await connection.execute("BEGIN;\n" + ";\n".join(kept) + ";\nCOMMIT;")
        with pytest.raises(asyncpg.RaiseError):
            await connection.execute(READBACK)
    finally:
        await connection.close()


async def test_people_in_payloads_are_not_this_files_to_clear(
    database: Database, database_url: str
) -> None:
    """Neutralising alone leaves them, and the read-back says so."""
    connection = await connect(database_url)
    try:
        await seed(connection)
        await connection.execute(NEUTRALISE)
        with pytest.raises(asyncpg.RaiseError, match="users in posts: 2"):
            await connection.execute(READBACK)
    finally:
        await connection.close()


async def store(connection: Any, payloads: list[dict[str, Any]]) -> None:
    await connection.execute(
        "INSERT INTO channels (tg_id, status, discovered_via) "
        "VALUES (4, 'seed', 'manual')"
    )
    for msg_id, payload in enumerate(payloads, start=1):
        if payload.get("_") == "messages.ChatFull":
            await connection.execute(
                "INSERT INTO raw_channels (channel_id, payload) "
                "VALUES (4, $1::jsonb)",
                json.dumps(payload),
            )
        else:
            await connection.execute(
                "INSERT INTO raw_messages (channel_id, msg_id, payload) "
                "VALUES (4, $1, $2::jsonb)",
                msg_id,
                json.dumps(payload),
            )


async def test_every_case_reads_back_clean_once_cleared(
    database: Database, database_url: str
) -> None:
    connection = await connect(database_url)
    try:
        await store(connection, [case.cleared for case in ALL])
        await connection.execute(READBACK)
    finally:
        await connection.close()


@pytest.mark.parametrize("case", CASES, ids=[case.name for case in CASES])
async def test_every_case_fails_the_read_back_as_received(
    database: Database, database_url: str, case: Case
) -> None:
    """Each by type, wherever it sits: the paid reactors and a reply to a user
    among them, which no list of paths ever named."""
    connection = await connect(database_url)
    try:
        await store(connection, [case.received])
        with pytest.raises(
            asyncpg.RaiseError, match="a restored copy still holds"
        ):
            await connection.execute(READBACK)
    finally:
        await connection.close()


@pytest.mark.parametrize(
    ("kind", "payload"), ONE_OF_EACH, ids=[kind for kind, _ in ONE_OF_EACH]
)
async def test_each_type_and_field_alone_fails_the_read_back(
    database: Database, database_url: str, kind: str, payload: dict[str, Any]
) -> None:
    connection = await connect(database_url)
    try:
        await store(connection, [payload])
        with pytest.raises(asyncpg.RaiseError, match="in posts: 1"):
            await connection.execute(READBACK)
    finally:
        await connection.close()


async def test_a_person_among_a_channels_users_fails_the_read_back(
    database: Database, database_url: str
) -> None:
    """Bots alone were there when this was measured, and it must stay so."""
    connection = await connect(database_url)
    try:
        await connection.execute(
            "INSERT INTO channels (tg_id, status, discovered_via) "
            "VALUES (4, 'seed', 'manual')"
        )
        await connection.execute(
            "INSERT INTO raw_channels (channel_id, payload) "
            "VALUES (4, $1::jsonb)",
            json.dumps(
                {
                    "users": [
                        {"_": "User", "id": 1, "bot": True},
                        {"_": "User", "id": 2},
                    ]
                }
            ),
        )
        with pytest.raises(
            asyncpg.RaiseError, match="users in channel payloads: 1"
        ):
            await connection.execute(READBACK)
    finally:
        await connection.close()


@pytest.mark.parametrize(
    "insert",
    [
        (
            f"INSERT INTO yt_channels (channel_id, status, discovered_via) "
            f"VALUES ('{FAKE_YT}', 'candidate', 'manual');"
            f"INSERT INTO yt_raw_channels (channel_id, payload, fetched_at) "
            f"VALUES ('{FAKE_YT}', '{{}}', now())"
        ),
        (
            "INSERT INTO yt_raw_lookups (kind, value, channel_id, fetched_at) "
            "VALUES ('handle', 'fakehandle', NULL, now())"
        ),
    ],
)
async def test_api_data_in_a_copy_fails_the_read_back_naming_the_table(
    database: Database, database_url: str, insert: str
) -> None:
    connection = await connect(database_url)
    try:
        await connection.execute(insert)
        with pytest.raises(asyncpg.RaiseError, match="yt_raw_"):
            await connection.execute(READBACK)
    finally:
        await connection.close()


def test_the_read_back_covers_what_dumps_leave_out() -> None:
    for pattern in EXCLUDED_DATA:
        assert pattern.replace("*", "") in READBACK, pattern


def test_every_note_a_person_wrote_is_neutralised() -> None:
    """A table that gains a note fails here until the restore clears it."""
    notes = {
        (table.name, column.name)
        for table in Base.metadata.tables.values()
        for column in table.columns
        if column.name.endswith("_note")
    }
    assert notes
    for table, column in notes:
        assert f"UPDATE {table} SET {column} = NULL" in NEUTRALISE, column
        assert f"FROM {table}" in READBACK, table


def test_every_table_is_vacuumed() -> None:
    """Deleting a channel cascades into tables the script never names."""
    tasks = (ANSIBLE_DIR / "itgraph" / "tasks" / "restore.yml").read_text()
    assert "'VACUUM (FULL)'] }}" in tasks


def test_this_file_is_really_read(tmp_path: Path) -> None:
    assert len(statements()) >= 9
    # And it no longer touches a payload: the schema's revision does.
    assert "raw_messages" not in NEUTRALISE.split("BEGIN;", 1)[1]
