"""The single source of environment configuration.

Import ``settings`` from here; no other module reads ``os.environ``.
"""

from pathlib import Path
from typing import Self

from pydantic import Field, PostgresDsn, SecretStr, model_validator
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

    # Above this the collector stops the run instead of sleeping it off.
    # Sits above `flood_sleep_threshold`, so a wait under a minute never
    # reaches the decision, and well below the day-long waits that read as
    # a per-method daily quota rather than a burst: those are answered by
    # calling the method less, not by holding a connection open until the
    # counter rolls over. Half an hour of waiting is tolerable; a day is
    # also fragile, because the machine will likely suspend first.
    flood_abort_threshold: float = 1800.0

    # Deliberately slow. History arrives 100 messages per request, so a
    # first pass over 200 channels is a few thousand requests: hours at
    # this pacing, which is the point. Raise it only with a reason.
    backfill_request_delay: float = 4.0
    backfill_batch_size: int = 100

    # The gap before a request is drawn from a band around the delay
    # rather than being the delay exactly. Relative, not absolute: at the
    # default delay this is [2, 6] seconds, and it stays sane for a delay
    # of 1 or of 30, which a fixed ±2 would not. This changes no request
    # count and so cannot affect a daily quota — it is cheap camouflage
    # against pattern detection nobody outside Telegram can confirm
    # exists, not the part of the pacing that protects the account.
    # Below 1, or a band reaching zero would let a gap vanish entirely.
    pacing_jitter: float = Field(default=0.5, ge=0.0, lt=1.0)
    # A small fraction of gaps come from a much longer range instead —
    # not in addition, so the rule reads in one sentence. At these
    # defaults it adds about 0.8s to a mean gap of 4.
    pacing_long_pause_chance: float = Field(default=0.02, ge=0.0, le=1.0)
    pacing_long_pause_min: float = Field(default=20.0, ge=0.0)
    pacing_long_pause_max: float = Field(default=60.0, ge=0.0)

    # Between one channel and the next, where the per-channel requests
    # that carry daily quotas cluster, and — before this existed — the one
    # boundary in the walk with no gap at all. Over 200 channels this is
    # roughly 80 minutes on a run that already takes hours.
    backfill_channel_pause_min: float = Field(default=10.0, ge=0.0)
    backfill_channel_pause_max: float = Field(default=40.0, ge=0.0)

    # How long a stored `GetFullChannelRequest` payload stays good. A
    # description and a linked discussion chat change on the order of
    # months, and re-reading them every run spends the least cacheable
    # request in the walk — roughly 200 of them per pass over the
    # inventory — to learn nothing.
    channel_metadata_max_age_days: int = 30
    # How many messages one channel may ever contribute to the corpus.
    # Without it a handful of news aggregators posting dozens of times a
    # day would be most of the database — and they are the least
    # informative nodes in the graph, reposting everyone and being
    # reposted by nobody. Reaching it ends that channel for good, not
    # just for this run. 0 means no ceiling.
    backfill_max_messages: int = 2000

    # Messages read per partition when deriving edges. Derivation touches
    # no network, so this is a memory/round-trip trade, not a pacing one:
    # large enough that the batch write amortizes, small enough that a run
    # holds a bounded slice of the raw layer in memory at once.
    derive_batch_size: int = 1000

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

    @model_validator(mode="after")
    def _pause_ranges_are_ordered(self) -> Self:
        """Refuse an inverted pause range at import rather than at hour three.

        A minimum above its maximum reaches the random draw as an empty
        interval, and what comes back is a gap outside the band — possibly
        a negative one. That surfaces mid-run, on a command that is
        expensive to restart, and looks like a pacing bug rather than a
        typo in `.env`.
        """
        ranges = {
            "pacing_long_pause": (
                self.pacing_long_pause_min,
                self.pacing_long_pause_max,
            ),
            "backfill_channel_pause": (
                self.backfill_channel_pause_min,
                self.backfill_channel_pause_max,
            ),
        }
        for name, (low, high) in ranges.items():
            if low > high:
                raise ValueError(
                    f"{name}_min ({low}) is above {name}_max ({high})"
                )
        return self


settings = Settings()
