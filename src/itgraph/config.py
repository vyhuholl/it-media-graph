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

    # --- the watch loop ---------------------------------------------
    #
    # Everything below governs a process that runs indefinitely, which is
    # what makes these different in kind from the backfill settings above.
    # A backfill spends its budget and stops; the loop spends a little,
    # forever. So the numbers are chosen for a sustained rate that is
    # lower than the backfill's, not for a burst that is survivable.

    # When a post is read, in minutes after it was published. Dense early,
    # where a spike separates from an ordinary post, and thin later,
    # because forwards and comments accrue for far longer than views do.
    # A sample missed while the loop was down is dropped rather than taken
    # late — see `itgraph.schedule` for why that is not a compromise.
    watch_sample_offsets: tuple[float, ...] = (
        15,
        30,
        60,
        120,
        240,
        480,
        1440,
        2880,
    )

    # Past this age a post is no longer read at all. It must sit strictly
    # past the last sample, and the extra hour is not slack for its own
    # sake: a horizon equal to the last offset makes that last sample
    # unreachable, because a post old enough to be due for it is already
    # too old to be read. The validator below refuses that arrangement
    # rather than letting a configured sample quietly never happen.
    watch_horizon_hours: float = Field(default=49.0, gt=0)

    # A channel with nothing live is checked on an interval derived from
    # its own posting rate — the mean gap between its posts, divided by
    # this. Above 1 so a channel is checked several times per expected
    # post rather than once, which is what bounds how old a post can be
    # before anything notices it.
    watch_idle_divisor: float = Field(default=4.0, ge=1.0)
    # ...and then clamped. The floor stops a prolific channel from being
    # polled every few minutes on the idle path; the ceiling stops a
    # channel that posts twice a year from being forgotten. Measured
    # against this inventory: ~400 channels have nothing live at any
    # moment, and at these bounds they cost roughly 800 requests a day
    # between them.
    watch_idle_min_minutes: float = Field(default=30.0, gt=0)
    watch_idle_max_minutes: float = Field(default=720.0, gt=0)

    # No channel is polled twice inside this, whatever the schedule says.
    # A channel publishing an album or a burst of five posts would
    # otherwise have five samples due within a minute of each other.
    watch_min_gap_minutes: float = Field(default=10.0, ge=0)

    # How many messages one poll asks for: the channel's posting rate over
    # the horizon, inside these bounds. The ceiling is what keeps a poll
    # to a single request — a channel needing more than 100 would have to
    # post over 50 times a day, which this inventory does not contain.
    watch_window_min: int = Field(default=10, ge=1)
    watch_window_max: int = Field(default=100, ge=1)

    # A channel that keeps coming back empty, and one that keeps failing,
    # are both backed off — the first mildly, the second hard. Neither is
    # a reason to stop checking, so both are capped rather than allowed to
    # grow without bound.
    watch_empty_backoff: float = Field(default=1.5, ge=1.0)
    watch_empty_backoff_cap: int = Field(default=4, ge=0)
    watch_failure_backoff: float = Field(default=2.0, ge=1.0)
    watch_failure_backoff_cap: int = Field(default=6, ge=0)
    watch_failure_max_minutes: float = Field(default=1440.0, gt=0)

    # The gap before each of the loop's requests. Larger than the
    # backfill's, because nothing here is in a hurry: the whole inventory
    # produces ~576 posts a day, and the loop has all day.
    watch_request_delay: float = 6.0

    # How long to wait when nothing is due. The floor on how promptly the
    # loop notices its own queue, and it costs no request.
    watch_tick_seconds: float = Field(default=60.0, gt=0)

    # How long a cached posting rate stays good. It is an input to a
    # schedule, not a number anyone reads, and recomputing it per tick
    # would cost more in queries than the polling costs in requests.
    watch_rate_max_age_hours: float = Field(default=24.0, gt=0)

    # Local hours between which the loop does not poll. Real accounts
    # sleep, it removes a large share of the daily request count, and
    # nothing here needs minute-level latency at 04:00. Equal values mean
    # no quiet window at all. The zone is explicit because a naive local
    # time is wrong the moment this runs anywhere but one laptop.
    watch_quiet_from_hour: int = Field(default=2, ge=0, le=23)
    watch_quiet_to_hour: int = Field(default=7, ge=0, le=23)
    watch_timezone: str = "Europe/Moscow"

    # How often a running loop re-confirms it still holds the session
    # lease. Losing it is fatal, so this is how long two collectors could
    # in principle overlap before one of them notices and stops.
    watch_lease_check_seconds: float = Field(default=300.0, gt=0)

    # --- alerting -----------------------------------------------------
    #
    # The bands are measured rather than chosen. Over the densely
    # collected last 30 and 60 days, with intra-family reposts excluded,
    # a post reaching this many distinct families within the window:
    #
    #     1 family  → ~19 alerts/day   "somebody reposted this" — noise
    #     2         → ~1.1
    #     3         → ~0.35            one every three days
    #     4+        → ~0               one case in two months
    #
    # There is almost no band between noise and silence, which is why
    # this stops at three: a fourth would never fire and would mislead
    # whoever read it next.
    alert_cascade_bands: tuple[int, ...] = (2, 3)

    # How long after publication a repost still counts toward a cascade.
    # Not a correctness filter — a post picked up after three days did
    # travel — but what makes this "moving now" rather than "has moved".
    # Measured: six hours catches 73% of what twenty-four does, and the
    # median crossing is at 4h37m, so most of what a longer window adds
    # arrives late enough to belong in a weekly summary.
    alert_cascade_window_hours: float = Field(default=6.0, gt=0)

    # Past this, the pass says its evidence is stale. Derivation is not
    # continuous and a full pass takes seconds, so anything approaching
    # an hour means nothing is running it.
    alert_stale_edges_hours: float = Field(default=2.0, gt=0)

    # How many alerts may be sent directly per day. What exceeds it is
    # held for the digest, never dropped. At the measured ~1.1/day this
    # will not bind for weeks; it exists now because it is cheap now and
    # because the scoring change is what will load it.
    alert_daily_cap: int = Field(default=20, ge=1)

    # When the summary of everything held goes out, in `watch_timezone`.
    alert_digest_hour: int = Field(default=9, ge=0, le=23)

    # The bot's own quiet window. Separate from the collector's because
    # they mean different things — one is about not making requests, the
    # other about not making noise — and equal by default because today
    # both are the operator's night.
    alert_quiet_from_hour: int = Field(default=2, ge=0, le=23)
    alert_quiet_to_hour: int = Field(default=7, ge=0, le=23)

    # How often the bot looks for work regardless of any notification.
    # This is the correctness mechanism: `NOTIFY` is not durable, so a
    # bot that was down when one fired learns about the alert here or
    # not at all.
    alert_poll_seconds: float = Field(default=60.0, gt=0)

    # How many failed sends before an alert is reported as stuck rather
    # than quietly retried forever.
    alert_failure_report_after: int = Field(default=3, ge=1)

    # The bot's credentials and its one recipient. The token is the
    # credential most likely to end up on a machine the operator does not
    # own; it is never committed, and the bot's database role is what
    # bounds the damage if it leaks.
    telegram_bot_token: SecretStr | None = None
    alert_chat_id: int | None = None

    # The bot's own connection, under the restricted `itgraph_bot` role.
    # A *separate* setting rather than "point DATABASE_URL at the bot
    # role when you run the bot", because that instruction is one the
    # operator has to remember forever and cannot verify: the collector
    # reads its settings once at startup, so a `.env` pointed at the bot
    # role keeps working until something restarts, and then every write
    # fails at once. Two settings cannot be confused that way — each
    # process reads the one meant for it.
    #
    # Unset means the bot uses `database_url` like everything else, which
    # works and is not hardened. The bot says which of the two it got, so
    # the unhardened state is visible rather than assumed.
    bot_database_url: PostgresDsn | None = None

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

    @model_validator(mode="after")
    def _watch_bounds_are_ordered(self) -> Self:
        """Refuse a schedule that cannot produce a valid interval.

        Same reasoning as the pause ranges: an inverted clamp surfaces
        hours into a run that is meant never to end, and it looks like a
        scheduling bug rather than a typo in `.env`.
        """
        if self.watch_idle_min_minutes > self.watch_idle_max_minutes:
            raise ValueError(
                f"watch_idle_min_minutes ({self.watch_idle_min_minutes}) is "
                f"above watch_idle_max_minutes ({self.watch_idle_max_minutes})"
            )
        if self.watch_window_min > self.watch_window_max:
            raise ValueError(
                f"watch_window_min ({self.watch_window_min}) is above "
                f"watch_window_max ({self.watch_window_max})"
            )
        if not self.watch_sample_offsets:
            raise ValueError(
                "watch_sample_offsets is empty: a post would be stored and "
                "then never read, which is not a lighter schedule but a "
                "different feature"
            )
        if list(self.watch_sample_offsets) != sorted(
            self.watch_sample_offsets
        ):
            raise ValueError(
                "watch_sample_offsets must be in ascending order; the "
                "schedule takes the first offset past a post's age and an "
                "unsorted list would skip samples silently"
            )
        last = self.watch_sample_offsets[-1]
        if last >= self.watch_horizon_hours * 60:
            raise ValueError(
                f"the last watch_sample_offsets entry ({last} min) is not "
                f"inside watch_horizon_hours ({self.watch_horizon_hours} h): "
                "a post old enough for that sample would already be past the "
                "horizon, so the sample could never be taken"
            )
        return self

    @model_validator(mode="after")
    def _digest_is_not_inside_quiet_hours(self) -> Self:
        """Refuse a digest hour the bot would never be allowed to speak at.

        The digest is what makes quiet hours a delay rather than a drop,
        so scheduling it inside them would hold every alert and then
        never deliver the summary — silence that looks exactly like
        having nothing to report.

        Checked here rather than re-checked at send time on purpose. A
        second guard in the delivery path would turn a bad configuration
        into a bot that runs correctly and says nothing, which is the one
        failure this whole feature is least able to notice.
        """
        start = self.alert_quiet_from_hour
        end = self.alert_quiet_to_hour
        if start == end:
            return self

        hour = self.alert_digest_hour
        inside = (
            start <= hour < end if start < end else hour >= start or hour < end
        )
        if inside:
            raise ValueError(
                f"alert_digest_hour ({hour}) falls inside the bot's quiet "
                f"window ({start}:00–{end}:00): the digest would be held by "
                "the same rule it exists to compensate for, and nothing "
                "held would ever be delivered"
            )
        return self


settings = Settings()
