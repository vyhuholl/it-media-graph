"""The single source of environment configuration.

Import ``settings`` from here; no other module reads ``os.environ``.
"""

from pathlib import Path

from pydantic import PostgresDsn, SecretStr
from pydantic_settings import BaseSettings, SettingsConfigDict

__all__ = ["Settings", "settings"]

# Backups hold the operator's own subscriptions and review decisions, so
# they live outside the repository where no `git add .` can reach them.
DEFAULT_BACKUP_DIR = Path.home() / "itgraph-backups"


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

    backup_dir: Path = DEFAULT_BACKUP_DIR

    # There is no pg_dump on the host — it runs inside the Postgres
    # container, addressed by the fixed name docker-compose.yaml gives it.
    postgres_container: str = "itgraph-postgres"
    # An absolute path because launchd starts jobs with a bare PATH that
    # does not include /usr/local/bin.
    docker_binary: Path = Path("/usr/local/bin/docker")

    # The inventory is small and irreplaceable, so it is kept deep. Full
    # dumps carry the raw layer, which is large and — expensively —
    # re-fetchable, so fewer are kept.
    backup_keep_inventory: int = 30
    backup_keep_full: int = 4
    # Under 24 so a daily rhythm cannot drift later each run until it
    # slips past midnight and skips a day.
    backup_inventory_interval_hours: int = 20
    backup_full_interval_days: int = 7


settings = Settings()
