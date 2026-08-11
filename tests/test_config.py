from pathlib import Path

import pytest
from pydantic import ValidationError

from itgraph.config import ProxyType, Settings

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
    """Exactly ``VALID_ENV``, and provably nothing else.

    Every other setting is cleared rather than left standing. ``build()``
    passes ``_env_file=None`` so the file cannot leak in, but that says
    nothing about the *environment*, and pydantic-settings reads it
    either way — so a `BOT_DATABASE_URL` exported in a shell, or loaded
    from `.env` by `conftest`, used to reach the tests that assert an
    optional setting is unset and fail them. Derived from the model's own
    fields, so a setting added later is cleared without anyone
    remembering to add it here.
    """
    for name in Settings.model_fields:
        if name.upper() not in VALID_ENV:
            monkeypatch.delenv(name.upper(), raising=False)
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


def test_a_sample_outside_the_horizon_is_refused(
    env: pytest.MonkeyPatch,
) -> None:
    """A sample past the horizon could never be taken.

    A post old enough to be due for it is already too old to be read, so
    the entry would sit in the configuration doing nothing. Refused at
    import rather than discovered as a gap in the data weeks later.
    """
    env.setenv("WATCH_HORIZON_HOURS", "48")
    env.setenv("WATCH_SAMPLE_OFFSETS", "[15,2880]")
    with pytest.raises(ValidationError, match="past the horizon"):
        build()


def test_unsorted_sample_offsets_are_refused(env: pytest.MonkeyPatch) -> None:
    """The schedule takes the first offset past a post's age.

    Out of order, that silently skips every entry after a larger one.
    """
    env.setenv("WATCH_SAMPLE_OFFSETS", "[60,15,120]")
    with pytest.raises(ValidationError, match="ascending"):
        build()


def test_an_empty_sample_schedule_is_refused(env: pytest.MonkeyPatch) -> None:
    env.setenv("WATCH_SAMPLE_OFFSETS", "[]")
    with pytest.raises(ValidationError, match="empty"):
        build()


def test_an_inverted_idle_clamp_is_refused(env: pytest.MonkeyPatch) -> None:
    env.setenv("WATCH_IDLE_MIN_MINUTES", "600")
    env.setenv("WATCH_IDLE_MAX_MINUTES", "30")
    with pytest.raises(ValidationError, match="watch_idle_min_minutes"):
        build()


def test_the_bot_gets_its_own_connection(env: pytest.MonkeyPatch) -> None:
    """A separate setting, not a repointed `DATABASE_URL`.

    The instruction "run the bot with DATABASE_URL pointing at the bot
    role" is one the operator has to remember forever and cannot verify:
    the collector reads its settings once at startup, so a `.env` pointed
    at the restricted role keeps working until something restarts — and
    then every collection write fails at once.
    """
    env.setenv(
        "BOT_DATABASE_URL", "postgresql+asyncpg://itgraph_bot:p@localhost/db"
    )
    settings = build()

    assert settings.bot_database_url is not None
    assert "itgraph_bot" in str(settings.bot_database_url)
    # And the collector's own connection is untouched by it.
    assert "itgraph_bot" not in str(settings.database_url)


def test_the_bot_connection_is_optional(env: pytest.MonkeyPatch) -> None:
    """Unset means the bot shares the collector's credentials.

    Supported and unhardened; the bot logs which of the two it got, so
    the state is visible rather than assumed.
    """
    assert build().bot_database_url is None


# Not a plausible password: `tests/` is deliberately not excluded from
# the gitleaks hook, so a realistic-looking fake would trip it — and the
# fix is an obviously fake value, not an exclude.
PROXY_PASSWORD = "test-proxy-password"

PROXY_ENV = {
    "PROXY_TYPE": "socks5",
    "PROXY_HOST": "proxy.invalid",
    "PROXY_PORT": "1080",
}


def test_no_proxy_is_the_default(env: pytest.MonkeyPatch) -> None:
    """Unset means a direct connection: what a laptop wants."""
    settings = build()
    assert settings.proxy_host is None
    assert settings.proxy_type is None


def test_a_complete_proxy_is_accepted(env: pytest.MonkeyPatch) -> None:
    for key, value in PROXY_ENV.items():
        env.setenv(key, value)
    settings = build()

    assert settings.proxy_type is ProxyType.SOCKS5
    assert settings.proxy_host == "proxy.invalid"
    assert settings.proxy_port == 1080
    # Credentials are optional: plenty of proxies take none.
    assert settings.proxy_username is None
    assert settings.proxy_password is None


def test_proxy_credentials_are_accepted(env: pytest.MonkeyPatch) -> None:
    for key, value in PROXY_ENV.items():
        env.setenv(key, value)
    env.setenv("PROXY_USERNAME", "collector")
    env.setenv("PROXY_PASSWORD", PROXY_PASSWORD)
    settings = build()

    assert settings.proxy_username == "collector"
    assert settings.proxy_password is not None
    assert settings.proxy_password.get_secret_value() == PROXY_PASSWORD


def test_the_proxy_password_is_not_printed(env: pytest.MonkeyPatch) -> None:
    """On the same footing as the api hash."""
    for key, value in PROXY_ENV.items():
        env.setenv(key, value)
    env.setenv("PROXY_PASSWORD", PROXY_PASSWORD)

    assert PROXY_PASSWORD not in repr(build())


def test_an_unsupported_proxy_type_is_refused(env: pytest.MonkeyPatch) -> None:
    """Refused here rather than by Telethon at the first connection."""
    for key, value in PROXY_ENV.items():
        env.setenv(key, value)
    env.setenv("PROXY_TYPE", "mtproxy")

    with pytest.raises(ValidationError, match="proxy_type"):
        build()


def test_a_host_without_a_port_is_refused(env: pytest.MonkeyPatch) -> None:
    env.setenv("PROXY_TYPE", "socks5")
    env.setenv("PROXY_HOST", "proxy.invalid")

    with pytest.raises(ValidationError, match="proxy_port"):
        build()


def test_a_host_without_a_type_is_refused(env: pytest.MonkeyPatch) -> None:
    env.setenv("PROXY_HOST", "proxy.invalid")
    env.setenv("PROXY_PORT", "1080")

    with pytest.raises(ValidationError, match="proxy_type"):
        build()


def test_a_port_without_a_host_is_refused(env: pytest.MonkeyPatch) -> None:
    """The dangerous half of a partial configuration.

    A host without a port fails loudly at the connection; a port without
    a host would connect directly while every setting around it says the
    connection is proxied.
    """
    env.setenv("PROXY_TYPE", "socks5")
    env.setenv("PROXY_PORT", "1080")

    with pytest.raises(ValidationError, match="without proxy_host"):
        build()


def test_credentials_without_a_host_are_refused(
    env: pytest.MonkeyPatch,
) -> None:
    env.setenv("PROXY_USERNAME", "collector")
    env.setenv("PROXY_PASSWORD", PROXY_PASSWORD)

    with pytest.raises(ValidationError, match="without proxy_host") as caught:
        build()

    # The message names what is set, so the operator can see which of
    # the values needs removing — and never the password itself.
    assert "proxy_username" in str(caught.value)
    assert PROXY_PASSWORD not in str(caught.value)
