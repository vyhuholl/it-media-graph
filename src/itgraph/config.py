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

    # Telethon sleeps through a FloodWait shorter than this on its own;
    # longer ones surface as an exception the collector waits out and
    # logs. Neither path uses another session or account — that is what
    # escalates a rate limit into a ban.
    flood_sleep_threshold: int = 60

    # Deliberately slow. History arrives 100 messages per request, so a
    # first pass over 200 channels is a few thousand requests: hours at
    # this pacing, which is the point. Raise it only with a reason.
    backfill_request_delay: float = 2.0
    backfill_batch_size: int = 100
    # How many messages one channel may ever contribute to the corpus.
    # Without it a handful of news aggregators posting dozens of times a
    # day would be most of the database — and they are the least
    # informative nodes in the graph, reposting everyone and being
    # reposted by nobody. Reaching it ends that channel for good, not
    # just for this run. 0 means no ceiling.
    backfill_max_messages: int = 2000

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
