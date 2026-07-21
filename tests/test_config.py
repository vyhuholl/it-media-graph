from pathlib import Path

import pytest
from pydantic import ValidationError

from itgraph.config import Settings

# Deliberately not shaped like a real api_hash (32 lowercase hex): a secret
# scanner cannot tell a fake one from the real thing, so the fake has to be
# obviously fake — to gitleaks and to a reviewer alike.
API_HASH = "test-api-hash"

VALID_ENV = {
    "DATABASE_URL": "postgresql+asyncpg://u:p@localhost:5432/db",
    "TELEGRAM_API_ID": "12345",
    "TELEGRAM_API_HASH": API_HASH,
}


@pytest.fixture
def env(monkeypatch: pytest.MonkeyPatch) -> pytest.MonkeyPatch:
    for key, value in VALID_ENV.items():
        monkeypatch.setenv(key, value)
    return monkeypatch


def build() -> Settings:
    # _env_file=None so a developer's real .env never leaks into a test.
    return Settings(_env_file=None)


def test_reads_environment(env: pytest.MonkeyPatch) -> None:
    settings = build()
    assert settings.telegram_api_id == 12345
    assert settings.telegram_session == Path("itgraph.session")
    assert settings.device_model


def test_api_hash_is_not_printed(env: pytest.MonkeyPatch) -> None:
    settings = build()
    assert API_HASH not in repr(settings)
    assert settings.telegram_api_hash.get_secret_value() == API_HASH


def test_database_url_must_be_postgres(env: pytest.MonkeyPatch) -> None:
    env.setenv("DATABASE_URL", "mysql://u:p@localhost/db")
    with pytest.raises(ValidationError):
        build()


def test_missing_credentials_fail_loudly(env: pytest.MonkeyPatch) -> None:
    env.delenv("TELEGRAM_API_HASH")
    with pytest.raises(ValidationError):
        build()
