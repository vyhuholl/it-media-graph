"""The revision that cleared people from what was stored before.

Two things are held here. The rule in SQL that the newest such revision
carries is the collector's, case for case: so a rule that grows in Python
fails this file until a revision clears stored rows by it too. And the
revision itself, run by Alembic on a database of its own, clears what it
should and nothing else, and its downgrade changes nothing. That database is
built here and dropped here: nothing in the suite may alter the shared
schema.
"""

import asyncio
import json
import os
import subprocess
import sys
from collections.abc import Iterator
from pathlib import Path
from typing import Any

import asyncpg
import pytest
from conftest import MAINTENANCE_DATABASE
from conftest import test_database_url as shared_test_url
from people_cases import ALL, CASES, ONE_OF_EACH, UNTOUCHED, Case, newest_rule
from sqlalchemy.engine import URL, make_url

from itgraph.tg.payload import without_people

REPO_ROOT = Path(__file__).parent.parent
PREVIOUS = "b7d2e4f19a30"
SOURCE = 1000000001


RULE = newest_rule()


async def connect(url: URL | str) -> Any:
    # str() of a URL hides its password; a string here already holds it.
    parsed = url if isinstance(url, URL) else make_url(url)
    return await asyncpg.connect(
        host=parsed.host,
        port=parsed.port,
        user=parsed.username,
        password=parsed.password,
        database=parsed.database,
    )


async def with_rule(database_url: str) -> Any:
    connection = await connect(database_url)
    for statement in RULE.FUNCTIONS:
        await connection.execute(statement)
    return connection


@pytest.mark.parametrize("case", ALL, ids=[case.name for case in ALL])
async def test_the_rule_in_sql_is_the_collectors(
    database_url: str, case: Case
) -> None:
    connection = await with_rule(database_url)
    try:
        cleared = json.loads(
            await connection.fetchval(
                "SELECT pg_temp.itgraph_without_people($1::jsonb)::text",
                json.dumps(case.received),
            )
        )
        again = json.loads(
            await connection.fetchval(
                "SELECT pg_temp.itgraph_without_people($1::jsonb)::text",
                json.dumps(cleared),
            )
        )
    finally:
        await connection.close()

    assert cleared == case.cleared == without_people(case.received)
    assert again == cleared


@pytest.mark.parametrize("case", ALL, ids=[case.name for case in ALL])
async def test_the_prefilter_finds_exactly_what_changes(
    database_url: str, case: Case
) -> None:
    connection = await connect(database_url)
    try:
        before, after = [
            await connection.fetchval(
                "SELECT jsonb_path_exists($1::jsonb, $2::jsonpath)",
                json.dumps(payload),
                RULE.NAMES_SOMEONE,
            )
            for payload in (case.received, case.cleared)
        ]
    finally:
        await connection.close()

    assert before is (case.received != case.cleared)
    assert after is False


@pytest.mark.parametrize(
    ("kind", "payload"), ONE_OF_EACH, ids=[kind for kind, _ in ONE_OF_EACH]
)
async def test_each_type_and_field_alone_is_the_collectors_in_sql(
    database_url: str, kind: str, payload: dict[str, Any]
) -> None:
    connection = await with_rule(database_url)
    try:
        cleared = json.loads(
            await connection.fetchval(
                "SELECT pg_temp.itgraph_without_people($1::jsonb)::text",
                json.dumps(payload),
            )
        )
        found = await connection.fetchval(
            "SELECT jsonb_path_exists($1::jsonb, $2::jsonpath)",
            json.dumps(payload),
            RULE.NAMES_SOMEONE,
        )
    finally:
        await connection.close()

    assert cleared == without_people(payload), kind
    assert found is True, kind


# --- the revision, on a database of its own ------------------------------


def alembic(url: URL, *args: str) -> None:
    done = subprocess.run(
        [sys.executable, "-m", "alembic", *args],
        cwd=REPO_ROOT,
        env={
            **os.environ,
            "DATABASE_URL": url.render_as_string(hide_password=False),
            # A dump before an upgrade is the backup layer's, tested there;
            # here there is no container to take one from.
            "ITGRAPH_SKIP_BACKUP": "1",
        },
        capture_output=True,
        text=True,
        timeout=180,
        check=False,
    )
    assert done.returncode == 0, done.stdout + done.stderr


@pytest.fixture
def own_database() -> Iterator[URL]:
    shared = shared_test_url()
    worker = os.environ.get("PYTEST_XDIST_WORKER", "main")
    url = shared.set(database=f"itgraph_people_{worker}_test")
    assert url.database is not None and url.database.endswith("_test")
    admin = shared.set(database=MAINTENANCE_DATABASE)

    async def run(statement: str) -> None:
        connection = await connect(admin)
        try:
            await connection.execute(statement)
        finally:
            await connection.close()

    drop = f'DROP DATABASE IF EXISTS "{url.database}" WITH (FORCE)'
    asyncio.run(run(drop))
    asyncio.run(run(f'CREATE DATABASE "{url.database}"'))
    try:
        yield url
    finally:
        asyncio.run(run(drop))


async def seed(url: URL) -> None:
    connection = await connect(url)
    try:
        await connection.execute(
            "INSERT INTO channels (tg_id, username, status, discovered_via) "
            "VALUES ($1, 'fake_source', 'seed', 'manual')",
            SOURCE,
        )
        for msg_id, case in enumerate(ALL, start=1):
            if case.received.get("_") == "messages.ChatFull":
                await connection.execute(
                    "INSERT INTO raw_channels (channel_id, payload) "
                    "VALUES ($1, $2::jsonb)",
                    SOURCE,
                    json.dumps(case.received),
                )
            else:
                await connection.execute(
                    "INSERT INTO raw_messages (channel_id, msg_id, payload) "
                    "VALUES ($1, $2, $3::jsonb)",
                    SOURCE,
                    msg_id,
                    json.dumps(case.received),
                )
    finally:
        await connection.close()


async def read(url: URL) -> dict[str, Any]:
    connection = await connect(url)
    try:
        messages = {
            row["msg_id"]: (json.loads(row["payload"]), row["xmin"])
            for row in await connection.fetch(
                "SELECT msg_id, payload::text AS payload, xmin::text AS xmin "
                "FROM raw_messages"
            )
        }
        channel = json.loads(
            await connection.fetchval("SELECT payload::text FROM raw_channels")
        )
        version = await connection.fetchval(
            "SELECT version_num FROM alembic_version"
        )
        tables = await connection.fetch(
            "SELECT table_name FROM information_schema.tables "
            "WHERE table_schema = 'public' ORDER BY 1"
        )
    finally:
        await connection.close()
    return {
        "messages": messages,
        "channel": channel,
        "version": version,
        "tables": [row["table_name"] for row in tables],
    }


def expected(field: str) -> dict[int, Any]:
    return {
        msg_id: getattr(case, field)
        for msg_id, case in enumerate(ALL, start=1)
        if case.received.get("_") != "messages.ChatFull"
    }


def test_the_revision_clears_what_was_stored_and_nothing_else(
    own_database: URL,
) -> None:
    alembic(own_database, "upgrade", PREVIOUS)
    asyncio.run(seed(own_database))
    before = asyncio.run(read(own_database))

    alembic(own_database, "upgrade", RULE.revision)
    after = asyncio.run(read(own_database))

    assert after["version"] == RULE.revision
    assert {k: v for k, (v, _) in after["messages"].items()} == expected(
        "cleared"
    )
    [chat_full] = [c for c in CASES if c.received["_"] == "messages.ChatFull"]
    assert after["channel"] == chat_full.cleared
    # A row that named nobody was not rewritten, only moved by the vacuum.
    untouched = {
        msg_id for msg_id, case in enumerate(ALL, start=1) if case in UNTOUCHED
    }
    for msg_id in untouched:
        assert after["messages"][msg_id][1] == before["messages"][msg_id][1]
    for msg_id in set(expected("cleared")) - untouched:
        assert after["messages"][msg_id][1] != before["messages"][msg_id][1]

    # Going back changes nothing: no table, no value.
    alembic(own_database, "downgrade", PREVIOUS)
    back = asyncio.run(read(own_database))
    assert back["version"] == PREVIOUS
    assert back["tables"] == after["tables"]
    assert back["messages"] == after["messages"]
    assert back["channel"] == after["channel"]

    # And running it again over what it cleared changes nothing either.
    alembic(own_database, "upgrade", RULE.revision)
    again = asyncio.run(read(own_database))
    assert again["messages"] == after["messages"]
