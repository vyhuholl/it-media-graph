"""The single source of environment configuration.

Import ``settings`` from here; no other module reads ``os.environ``.
"""

from pathlib import Path

from pydantic import PostgresDsn, SecretStr
from pydantic_settings import BaseSettings, SettingsConfigDict

__all__ = ["Settings", "settings"]


class Settings(BaseSettings):
    """Values read from the environment and ``.env``, validated on import."""

    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
    )

    database_url: PostgresDsn
    telegram_api_id: int
    telegram_api_hash: SecretStr

    # A session file is full account access: keep it out of the repo.
    telegram_session: Path = Path("itgraph.session")

    # Reported to Telegram on connect. Telethon's defaults advertise a
    # library, not a client, which is one of the cheaper bot signals to
    # avoid; override these to match the device the account really uses.
    device_model: str = "Desktop"
    system_version: str = "Windows 10"
    app_version: str = "5.3.1 x64"


settings = Settings()
