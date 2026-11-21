"""The two commands over the throwaway database, with the model faked.

The commands own only their wiring — load the model from its settings
path, one ``Database``, one call, print — so that is all these tests
check: the loop runs against the real schema, the summary is the
command's line, and a database with nothing embedded is answered with a
sentence rather than an error.

Sync tests on purpose: the command runs its own ``asyncio.run``, and a
loop still running in a fixture would refuse the start. Seeding uses
``asyncio.run`` too, so every loop is closed before the next begins.
"""

import asyncio
from datetime import UTC, datetime
from pathlib import Path

import pytest
from embedfakes import FakeEmbeddingModel, vector
from typer.testing import CliRunner

from itgraph.cli import app
from itgraph.db import session as db_session
from itgraph.db.models import (
    Channel,
    ChannelStatus,
    DiscoverySource,
    RawMessage,
    TgPostEmbedding,
)
from itgraph.embed import model as embed_model

runner = CliRunner()
NOW = datetime(2026, 11, 21, 12, 0, tzinfo=UTC)


def use_test_database(monkeypatch: pytest.MonkeyPatch, url: str) -> None:
    original = db_session.Database
    # Any argument is ignored — the seed helper passes the url it was
    # given, the command passes none; both get the same database.
    monkeypatch.setattr(db_session, "Database", lambda *args: original(url))


def use_model(
    monkeypatch: pytest.MonkeyPatch, fake: FakeEmbeddingModel
) -> None:
    """Load always answers with the fake, whatever the directory says."""

    def load(model_dir: object) -> FakeEmbeddingModel:
        return fake

    monkeypatch.setattr(embed_model.EmbeddingModel, "load", staticmethod(load))


def _seed(url: str, *, with_embedding: bool) -> None:
    async def run() -> None:
        database = db_session.Database(url)
        try:
            async with database.session() as session:
                session.add(
                    Channel(
                        tg_id=3,
                        username="fake_named",
                        status=ChannelStatus.SEED,
                        discovered_via=DiscoverySource.MANUAL,
                    )
                )
                await session.flush()
                session.add(
                    RawMessage(
                        channel_id=3,
                        msg_id=10,
                        payload={
                            "id": 10,
                            "message": "the matching post",
                            "date": "2026-11-20T09:00:00+00:00",
                        },
                    )
                )
                session.add(
                    RawMessage(channel_id=3, msg_id=11, payload={"id": 11})
                )
                await session.flush()
                if with_embedding:
                    session.add(
                        TgPostEmbedding(
                            channel_id=3,
                            msg_id=10,
                            model_key="multilingual-e5-small-o4",
                            vec=vector(1.0),
                        )
                    )
        finally:
            await database.dispose()

    asyncio.run(run())


def test_embed_runs_the_loop_and_prints_its_line(
    monkeypatch: pytest.MonkeyPatch, database_url: str
) -> None:
    use_test_database(monkeypatch, database_url)
    use_model(monkeypatch, FakeEmbeddingModel())
    _seed(database_url, with_embedding=False)

    result = runner.invoke(app, ["embed"])

    assert result.exit_code == 0
    # One texted post embedded; the media-only message is not a text.
    assert result.output.strip() == (
        "embedded 1 Telegram and 0 YouTube texts (multilingual-e5-small-o4)"
    )


def test_search_prints_its_hits(
    monkeypatch: pytest.MonkeyPatch, database_url: str
) -> None:
    use_test_database(monkeypatch, database_url)
    use_model(monkeypatch, FakeEmbeddingModel(query=vector(1.0)))
    _seed(database_url, with_embedding=True)

    result = runner.invoke(app, ["search", "what it is about"])

    assert result.exit_code == 0
    assert "1.000" in result.output
    assert "telegram" in result.output
    assert "fake_named" in result.output
    assert "https://t.me/fake_named/10" in result.output


def test_search_with_nothing_embedded_says_so_cleanly(
    monkeypatch: pytest.MonkeyPatch, database_url: str
) -> None:
    use_test_database(monkeypatch, database_url)
    use_model(monkeypatch, FakeEmbeddingModel(query=vector(1.0)))
    _seed(database_url, with_embedding=False)

    result = runner.invoke(app, ["search", "anything"])

    assert result.exit_code == 0
    assert "nothing to search yet" in result.output


def test_a_missing_model_fails_with_the_loaders_sentence(
    monkeypatch: pytest.MonkeyPatch, database_url: str, tmp_path: Path
) -> None:
    """The command reaches the model through `EmbeddingModel.load` only.

    A missing directory is that method's error to raise, with the path
    in the message — the command adds nothing of its own.
    """
    use_test_database(monkeypatch, database_url)
    # No fake: the real load runs, against an empty directory.
    monkeypatch.setattr("itgraph.config.settings.embed_model_dir", tmp_path)

    result = runner.invoke(app, ["embed"])

    assert result.exit_code != 0
    assert isinstance(result.exception, FileNotFoundError)
    assert str(tmp_path) in str(result.exception)
