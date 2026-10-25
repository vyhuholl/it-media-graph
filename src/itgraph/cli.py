"""Typer entrypoint. Commands parse arguments and delegate; no logic here.

Imports of anything that reads the environment stay inside the command
bodies, so `itgraph --help` works on a machine with no `.env` yet.
"""

import asyncio
import logging
from collections.abc import Coroutine, Mapping
from contextlib import suppress
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import TYPE_CHECKING, Annotated, Any

import typer

if TYPE_CHECKING:
    # Under `TYPE_CHECKING` because importing it for real would pull in
    # `settings`, and `itgraph --help` has to work before there is a
    # `.env` to read.
    from itgraph.affiliation.run import DetectionSummary
    from itgraph.db.affiliation import CandidateRow
    from itgraph.tg.backfill import FloodWaitTooLong

from itgraph import __version__
from itgraph.affiliation.signals import DEFAULT_THRESHOLDS, DEFAULT_WEIGHTS
from itgraph.db.models import (
    BackfillState,
    Channel,
    ChannelKind,
    ChannelStatus,
    EdgeKind,
    RejectReason,
    YtChannel,
    YtChannelKind,
    YtLang,
    YtRejectReason,
)

app = typer.Typer(
    no_args_is_help=True,
    help="Collect and store the IT-media Telegram graph.",
)


def _run(body: Coroutine[Any, Any, None]) -> None:
    """Run a command body, turning an expected failure into exit 1.

    ``SessionBusyError`` belongs here rather than in each command: a
    collector already running is an ordinary thing to discover, and the
    operator needs the sentence, not a traceback ending in a Postgres
    function nobody asked about.
    """
    from itgraph.db.channels import ChannelLookupError
    from itgraph.db.session_lease import LeaseLostError, SessionBusyError

    # From `tg.errors`, not `tg.client`: every command runs through this
    # wrapper, and `tg.client` imports Telethon, which announces itself
    # on import. A pass that goes nowhere near Telegram must not print a
    # line implying it did.
    from itgraph.tg.errors import NotAuthorizedError, WatchStalled

    try:
        asyncio.run(body)
    except (
        ChannelLookupError,
        NotAuthorizedError,
        SessionBusyError,
        LeaseLostError,
        WatchStalled,
    ) as exc:
        typer.secho(str(exc), fg=typer.colors.RED, err=True)
        raise typer.Exit(1) from exc


def _report_halt(halt: FloodWaitTooLong | None) -> None:
    """Report a rate-limit halt and fail the command.

    The summary printed just before this is real work that was committed,
    so it is not an error — but the run did not finish, and a scheduled
    one must not be able to pass for a clean one. Hence: the counts on
    stdout, the reason on stderr, exit 1.

    Re-running after the reported time picks up from the stored cursor;
    there is nothing else for the operator to do.
    """
    if halt is None:
        return
    typer.secho(str(halt), fg=typer.colors.RED, err=True)
    raise typer.Exit(1)


@app.callback()
def main(
    verbose: Annotated[
        bool, typer.Option("--verbose", "-v", help="Debug-level logging.")
    ] = False,
) -> None:
    logging.basicConfig(
        level=logging.DEBUG if verbose else logging.INFO,
        format="%(asctime)s %(levelname)-8s %(name)s %(message)s",
    )
    # aiogram announces every update no handler took, at INFO. For a bot
    # with two handlers that is nearly every update there is — group
    # chatter, being added to a chat, strangers who found the username —
    # all of it unhandled by design rather than by accident. At roughly
    # one alert a day the line would be the whole log.
    #
    # `--verbose` restores it rather than merely declining to silence it,
    # and the difference is the point: it is the right diagnostic when a
    # handler has stopped matching — a wrong chat id looks exactly like
    # this and nothing else reports it — so debugging has to be able to
    # turn it back on, not just avoid turning it off.
    logging.getLogger("aiogram.event").setLevel(
        logging.NOTSET if verbose else logging.WARNING
    )


@app.command()
def version() -> None:
    """Print the installed itgraph version."""
    typer.echo(__version__)


@app.command()
def login(
    qr: Annotated[
        bool,
        typer.Option(
            "--qr",
            help="Confirm by QR code on a signed-in device, not by code.",
        ),
    ] = False,
) -> None:
    """Authorize the MTProto session (asks for phone number and code)."""
    from getpass import getpass

    from itgraph.db.session_lease import session_lease
    from itgraph.tg.auth import authorize_qr
    from itgraph.tg.client import build_client

    # The one networked command that does not go through `connected` —
    # it exists to create the authorized session the others require, so
    # it cannot ask for one. It still writes the session file, which is
    # the resource the lease protects, so it takes the lease by hand.
    async def run() -> None:
        async with session_lease("login"):
            client = build_client()
            try:
                if qr:
                    await client.connect()
                    if not await client.is_user_authorized():
                        await authorize_qr(
                            client,
                            show=typer.echo,
                            ask_password=lambda: getpass("2FA password: "),
                        )
                else:
                    await client.start()
                me = await client.get_me()
                typer.echo(
                    f"Authorized as {getattr(me, 'username', None) or me}"
                )
            finally:
                await client.disconnect()

    _run(run())


@app.command("dump-dialogs")
def dump_dialogs() -> None:
    """Import the public channels and groups the account is subscribed to."""
    from itgraph.db.session import Database
    from itgraph.tg.client import connected
    from itgraph.tg.dialogs import import_dialogs

    async def run() -> None:
        database = Database()
        try:
            async with (
                connected("dump-dialogs") as client,
                database.session() as session,
            ):
                counts = await import_dialogs(client, session)
        finally:
            await database.dispose()
        typer.echo(
            f"inserted {counts.inserted}, updated {counts.updated}, "
            f"skipped {counts.skipped} private",
        )

    _run(run())


@app.command()
def add(
    usernames: Annotated[
        list[str] | None,
        typer.Argument(
            metavar="[USERNAME]...",
            help="Channels to add: @name, name, or a t.me link.",
        ),
    ] = None,
    from_file: Annotated[
        Path | None,
        typer.Option(
            "--from-file",
            exists=True,
            dir_okay=False,
            readable=True,
            help="Read usernames from a file, one per line.",
        ),
    ] = None,
    seed: Annotated[
        bool,
        typer.Option("--seed", help="Mark what is added as in scope."),
    ] = False,
    kind: Annotated[
        ChannelKind | None,
        typer.Option("--kind", help="What the channels are. With --seed."),
    ] = None,
    limit: Annotated[
        int | None,
        typer.Option("--limit", help="Make at most this many requests."),
    ] = None,
    delay: Annotated[
        float | None,
        typer.Option("--delay", help="Seconds between requests."),
    ] = None,
    failures_out: Annotated[
        Path | None,
        typer.Option(
            "--failures-out",
            dir_okay=False,
            writable=True,
            help="Write the usernames that failed, as the next run's input.",
        ),
    ] = None,
) -> None:
    """Add channels to the inventory by username, without subscribing.

    Resolves each name and records it. Nothing is joined and no dialog
    list is read — which is the point: subscribing from a client and
    re-importing would spend the same `contacts.resolveUsername` and add
    a `channels.joinChannel` on top of it.

    That lookup is rationed by the day and cannot be batched, so a name
    already in the inventory costs no request: re-run the same file and
    the work continues where it stopped. `--limit` counts requests, not
    lines.

    `--seed` is refused with `--from-file`. A list nobody has re-read is
    where a typo gets accepted into scope unseen; run `itgraph channels
    --status candidate` and `itgraph mark` instead.
    """
    from itgraph.db.session import Database
    from itgraph.tg.client import connected
    from itgraph.tg.manual import Review, add_channels
    from itgraph.usernames import EntryError, parse_entries, read_entries

    if bool(usernames) == bool(from_file):
        raise typer.BadParameter(
            "give usernames or --from-file, not both and not neither"
        )
    if from_file is not None and (seed or kind is not None):
        raise typer.BadParameter(
            "--seed and --kind only apply to usernames given as arguments; "
            "review a list with `itgraph channels --status candidate` and "
            "`itgraph mark` once you have seen what it resolved to"
        )
    if kind is not None and not seed:
        raise typer.BadParameter("--kind needs --seed")
    if limit is not None and limit < 1:
        raise typer.BadParameter("--limit must be at least 1")

    try:
        names = (
            read_entries(from_file)
            if from_file is not None
            else parse_entries(usernames or [])
        )
    except EntryError as exc:
        raise typer.BadParameter(str(exc)) from exc

    if not names:
        typer.echo("Nothing to add.")
        return

    review = (
        Review(
            status=ChannelStatus.SEED,
            # `personal` is the explicit default, the same way `mark`
            # spells it: an empty kind has to keep meaning "not looked at".
            kind=kind or ChannelKind.PERSONAL,
        )
        if seed
        else None
    )

    async def run() -> None:
        database = Database()
        try:
            async with connected("add") as client:
                summary = await add_channels(
                    client,
                    database,
                    usernames=names,
                    review=review,
                    delay=delay,
                    limit=limit,
                )
        finally:
            await database.dispose()

        typer.echo(summary.line())
        for username, reason in summary.failures:
            typer.echo(f"  @{username}: {reason}")
        if failures_out is not None and summary.failures:
            failures_out.write_text(
                "".join(
                    f"{username}  # {reason}\n"
                    for username, reason in summary.failures
                ),
                encoding="utf-8",
            )
            typer.echo(f"failures written to {failures_out}")
        _report_halt(summary.halt)

    _run(run())


def _channel_ref(value: str) -> int | str:
    """Route the argument: a bare number is an id, the rest a username.

    ``@`` is optional, so a username pasted from Telegram works either
    way. A username can never be all digits, so nothing is ambiguous.
    """
    text = value.removeprefix("@")
    return int(text) if text.lstrip("-").isdigit() else text


@app.command()
def mark(
    channel_ref: Annotated[
        str,
        typer.Argument(
            metavar="CHANNEL",
            help="Telegram id or @username of the channel to review.",
        ),
    ],
    seed: Annotated[
        bool, typer.Option("--seed", help="In scope: collect from it.")
    ] = False,
    maybe: Annotated[
        bool, typer.Option("--maybe", help="Undecided; look again later.")
    ] = False,
    reject: Annotated[
        bool, typer.Option("--reject", help="Out of scope.")
    ] = False,
    kind: Annotated[
        ChannelKind | None,
        typer.Option("--kind", help="What the channel is. With --seed."),
    ] = None,
    reason: Annotated[
        RejectReason | None,
        typer.Option("--reason", help="Required with --reject."),
    ] = None,
    note: Annotated[
        str | None,
        typer.Option("--note", help="Free text beside the reason."),
    ] = None,
) -> None:
    """Record the review outcome for one channel."""
    from itgraph.db.channels import mark_channel
    from itgraph.db.session import Database

    if sum((seed, maybe, reject)) != 1:
        raise typer.BadParameter(
            "give exactly one of --seed, --maybe, --reject"
        )
    if reject and reason is None:
        raise typer.BadParameter("--reject needs --reason")

    if seed:
        # `personal` is the explicit default: an empty kind has to keep
        # meaning "not looked at yet".
        status, kind = ChannelStatus.SEED, kind or ChannelKind.PERSONAL
    elif maybe:
        status = ChannelStatus.MAYBE
    else:
        status = ChannelStatus.REJECTED

    async def run() -> None:
        database = Database()
        try:
            async with database.session() as session:
                channel = await mark_channel(
                    session,
                    _channel_ref(channel_ref),
                    status=status,
                    kind=kind,
                    reject_reason=reason,
                    reject_note=note,
                )
                typer.echo(f"{channel.tg_id} -> {channel.status.value}")
        finally:
            await database.dispose()

    _run(run())


@app.command()
def backfill(
    since: Annotated[
        datetime | None,
        typer.Option(
            "--since",
            formats=["%Y-%m-%d"],
            help="Fetch history no older than this date (YYYY-MM-DD).",
        ),
    ] = None,
    limit: Annotated[
        int | None,
        typer.Option("--limit", help="Process at most this many channels."),
    ] = None,
    delay: Annotated[
        float | None,
        typer.Option("--delay", help="Seconds between requests."),
    ] = None,
    batch_size: Annotated[
        int | None,
        typer.Option("--batch-size", help="Messages per request."),
    ] = None,
    max_messages: Annotated[
        int | None,
        typer.Option(
            "--max-messages",
            help=(
                "Never collect more than this many messages from one "
                "channel, ever (0 for no ceiling)."
            ),
        ),
    ] = None,
) -> None:
    """Fetch channel history into the raw layer. Resumable, and slow.

    A first run over the whole inventory is hours of requests. Start with
    `--limit` on a few channels and watch what happens before letting it
    loose on everything.

    Descriptions and linked chats are not touched here — see
    `itgraph metadata`, which spends the quota-bearing request they cost.
    """
    from itgraph.db.session import Database
    from itgraph.tg.backfill import backfill_channels
    from itgraph.tg.client import connected

    if since is None:
        raise typer.BadParameter(
            "--since is required, e.g. --since 2026-01-01"
        )
    if max_messages is not None and max_messages < 0:
        # Negative would otherwise read as "no ceiling", which is the
        # opposite of what an operator typing a ceiling means.
        raise typer.BadParameter("--max-messages cannot be negative")
    # Typer hands back a naive datetime; the column is timezone-aware and
    # comparing the two raises rather than silently guessing an offset.
    cutoff = since.replace(tzinfo=UTC)

    async def run() -> None:
        database = Database()
        try:
            async with (
                connected("backfill") as client,
                database.session() as session,
            ):
                summary = await backfill_channels(
                    client,
                    session,
                    cutoff=cutoff,
                    limit=limit,
                    batch_size=batch_size,
                    request_delay=delay,
                    max_messages=max_messages,
                    database=database,
                )
        finally:
            await database.dispose()
        typer.echo(summary.line())
        _report_halt(summary.halt)

    _run(run())


@app.command()
def metadata(
    limit: Annotated[
        int | None,
        typer.Option("--limit", help="Fetch at most this many channels."),
    ] = None,
    delay: Annotated[
        float | None,
        typer.Option("--delay", help="Seconds between requests."),
    ] = None,
    refresh: Annotated[
        bool,
        typer.Option(
            "--refresh",
            help=(
                "Re-fetch every in-scope channel, even ones whose stored "
                "information is still fresh."
            ),
        ),
    ] = False,
) -> None:
    """Fetch channel descriptions and linked discussion chats.

    Split off from `backfill` because it is expensive in a way history is
    not: `channels.getFullChannel` carries a per-day quota, and a
    description changes on the order of months. Run it about monthly, and
    use `--limit` to spread a first pass over several sittings — a halt
    here costs nothing but the rest of this queue.

    Channels the session file has no peer for are skipped rather than
    resolved: `itgraph resolve` is the one command allowed to spend that.
    """
    from itgraph.db.session import Database
    from itgraph.tg.client import connected
    from itgraph.tg.metadata import refresh_metadata

    async def run() -> None:
        database = Database()
        try:
            async with connected("metadata") as client:
                summary = await refresh_metadata(
                    client,
                    database,
                    limit=limit,
                    delay=delay,
                    refresh=refresh,
                )
        finally:
            await database.dispose()
        typer.echo(summary.line())
        _report_halt(summary.halt)

    _run(run())


@app.command()
def watch(
    cycles: Annotated[
        int | None,
        typer.Option(
            "--cycles",
            help="Stop after this many passes over the queue (default: never).",
        ),
    ] = None,
) -> None:
    """Poll seed channels for new posts and refreshed engagement counters.

    Runs until stopped. Unlike every other command here it holds the
    Telegram session for as long as it lives, so a `backfill`, `resolve`
    or `metadata` run started alongside it will refuse: stop the loop
    first.

    Each poll is one `messages.getHistory` and answers two questions at
    once — whether the channel published anything, and what its recent
    posts' counters are now. Nothing is derived; run `itgraph derive`
    over the collected messages as usual.

    A reading missed while this was not running is skipped rather than
    taken late. A snapshot due at post-age 30 minutes and taken at eight
    hours is a different measurement, not a late one.

    Rate limits and a lost connection are absorbed: the loop waits,
    reconnects, and carries on. It exits non-zero for two reasons only —
    it can no longer confirm it holds the session, or it has had
    channels due and concluded no poll at all for long enough that
    something is wrong that it cannot name. Both are worth a restart,
    and under systemd they get one.
    """
    import signal

    from itgraph.db.session import Database
    from itgraph.tg.client import connected_with_lease
    from itgraph.tg.watch import watch as watch_loop

    async def run() -> None:
        database = Database()
        stop = asyncio.Event()
        try:
            async with connected_with_lease("watch") as (client, lease):
                loop = asyncio.get_running_loop()
                # So that Ctrl-C and a supervisor's TERM both finish the
                # poll in flight and release the lease, rather than
                # leaving a half-written transaction and a lock to time
                # out. `add_signal_handler` is Unix-only; on anything
                # else the default handler still stops the process, just
                # less politely.
                for received in (signal.SIGINT, signal.SIGTERM):
                    with suppress(NotImplementedError):
                        loop.add_signal_handler(received, stop.set)
                stats = await watch_loop(
                    client, database, lease=lease, stop=stop, max_cycles=cycles
                )
        finally:
            await database.dispose()
        typer.echo(stats.line())

    _run(run())


@app.command("watch-status")
def watch_status() -> None:
    """Report what the poll loop has been doing, without disturbing it.

    Takes no session lease, deliberately: a status command that could not
    be run while the loop is running would be reporting on the one state
    nobody can observe.
    """
    from datetime import timedelta

    from itgraph.config import settings
    from itgraph.db.metrics import count_snapshots
    from itgraph.db.poll import queue_lag
    from itgraph.db.session import Database
    from itgraph.schedule import quiet_until

    async def run() -> None:
        database = Database()
        try:
            async with database.session() as session:
                now = datetime.now(UTC)
                lag = await queue_lag(session, now=now)
                day = await count_snapshots(
                    session, since=now - timedelta(days=1)
                )
                hour = await count_snapshots(
                    session, since=now - timedelta(hours=1)
                )
                total = await count_snapshots(session)
        finally:
            await database.dispose()

        typer.echo(
            f"{lag.tracked} channel(s) scheduled, {lag.overdue} due now"
        )
        if lag.oldest_due_at is not None:
            typer.echo(f"oldest overdue by {now - lag.oldest_due_at}")

        # A paused loop and a stuck one produce the same two lines above
        # — "2 due now, oldest overdue by 5h" is exactly what the quiet
        # window looks like from outside. Saying which one this is, is
        # the entire job of a status command here.
        release = quiet_until(
            now,
            start=settings.watch_quiet_from_hour,
            end=settings.watch_quiet_to_hour,
            zone=settings.watch_timezone,
        )
        if release is not None:
            typer.echo(
                f"quiet hours until {release:%H:%M} {release.tzname()} "
                "— not polling; the queue catches up afterwards"
            )
        typer.echo(
            f"snapshots: {hour} in the last hour, {day} in the last day, "
            f"{total} in all"
        )

    _run(run())


@app.command()
def alerts(
    window: Annotated[
        float | None,
        typer.Option(
            "--window",
            help="Hours after publication a repost still counts.",
        ),
    ] = None,
) -> None:
    """Find posts being carried by several unaffiliated channels.

    Reads the derived edges and writes alerts; issues no Telegram request
    and takes no session lease, so it runs beside a collector. Safe to
    put on a short schedule — a second pass over unchanged edges writes
    nothing.

    Its evidence is only as fresh as `itgraph derive`, and the summary
    says how stale that is. That line matters: an alerting system's
    healthy state is silence, so "nothing travelled" and "nothing was
    derived" have to be distinguishable.

    Expect about one alert a day. That is the measured rate at the only
    threshold that is not noise, and it is why the delivery machinery
    exists before the scoring that will fill it.
    """
    from datetime import timedelta

    from itgraph.alerts.run import run_cascades
    from itgraph.db.session import Database

    async def run() -> None:
        database = Database()
        try:
            summary = await run_cascades(
                database,
                window=(
                    timedelta(hours=window) if window is not None else None
                ),
            )
        finally:
            await database.dispose()
        typer.echo(summary.line())

    _run(run())


@app.command()
def baselines() -> None:
    """Recompute what a normal post looks like on every channel.

    Reads the raw layer and the snapshots, writes baselines, and touches
    nothing else — no Telegram request, no session lease. Run it before
    `itgraph score`, which has nothing to compare against otherwise, and
    then about weekly: a channel that has doubled its audience is being
    scored against the channel it used to be.

    A refresh replaces the previous one rather than adding to it, and
    only becomes visible once it has completed. Interrupting it is
    therefore safe — the previous baselines stay in use.
    """
    from itgraph.db.session import Database
    from itgraph.scoring.refresh import refresh_baselines

    async def run() -> None:
        database = Database()
        try:
            summary = await refresh_baselines(database)
        finally:
            await database.dispose()
        typer.echo(summary.line())

    _run(run())


@app.command()
def score(
    threshold: Annotated[
        float | None,
        typer.Option(
            "--threshold",
            help="Alert past this z instead of the configured one.",
        ),
    ] = None,
    replay: Annotated[
        bool,
        typer.Option(
            "--replay",
            help="Report what would have fired. Writes and sends nothing.",
        ),
    ] = False,
    since: Annotated[
        float | None,
        typer.Option(
            "--since",
            help="Replay from this many days back instead of the default.",
        ),
    ] = None,
) -> None:
    """Find posts doing unusually well for their channel and their age.

    Reads the snapshots and the baselines, writes alerts; issues no
    Telegram request and takes no session lease, so it runs beside a
    collector. Safe on a short schedule — a post raises at most one alert
    per metric, ever, and a second pass over unchanged snapshots writes
    nothing.

    `--replay` answers "what would this have said" over history, writing
    no alert and sending nothing. With `--threshold` it is how a
    threshold is chosen in minutes rather than one experiment per day.

    Spikes of one channel inside `alert_spike_fold_hours` of its first
    are one event: the first is raised, the rest are folded into it and
    told inside its message. The listing marks each fold and its head.
    """
    from itgraph.config import settings
    from itgraph.db.session import Database
    from itgraph.scoring.run import run_scoring

    if since is not None and not replay:
        # --since bounds a replay's history. On a live pass the window is
        # the last few hours by design, and widening it would raise
        # alerts about posts that settled days ago.
        raise typer.BadParameter("--since is only meaningful with --replay")

    async def run() -> None:
        now = datetime.now(UTC)
        span = since if since is not None else settings.scoring_replay_days
        window = timedelta(days=span) if replay else None
        database = Database()
        try:
            summary = await run_scoring(
                database,
                now=now,
                since=now - window if window is not None else None,
                threshold=threshold,
                dry_run=replay,
            )
        finally:
            await database.dispose()

        typer.echo(summary.line())
        heads = {
            entry.spike.post_key: entry.into.post_key
            for entry in summary.placed
            if entry.into is not None
        }
        for spike in summary.spikes:
            channel, msg = spike.post_key
            line = (
                f"  z {spike.score.z:5.1f}  {spike.score.metric:<9} "
                f"{channel}/{msg}  "
                f"{spike.score.observed:.0f} vs {spike.score.expected:.0f} "
                f"expected at {_hours(spike.score.age)}"
            )
            head = heads.get(spike.post_key)
            if head is not None:
                line += f"  ↳ folded into {head[0]}/{head[1]}"
            typer.echo(line)

    _run(run())


def _hours(age: timedelta) -> str:
    """A post's age, as the replay listing prints it."""
    minutes = max(int(age.total_seconds()) // 60, 0)
    if minutes < 60:
        return f"{minutes}m"
    return f"{minutes // 60}h{minutes % 60:02d}m"


@app.command()
def bot() -> None:
    """Deliver alerts to the operator, and record what they thought.

    Talks to the Bot API, holds no Telethon session and takes no session
    lease, so it runs while collection runs. Point it at the database
    with the restricted `itgraph_bot` role rather than the collector's
    credentials — see the bot section of src/itgraph/README.md.
    """
    import signal

    from aiogram import Bot

    from itgraph.bot.app import run_bot, supervise
    from itgraph.bot.handlers import BotSender, build_dispatcher

    # Inside the body, like every other import that reads the
    # environment: `itgraph --help` has to work on a machine that has no
    # `.env` yet, and a module-level `settings` would make it exit 1.
    from itgraph.config import settings
    from itgraph.db.session import Database

    if settings.telegram_bot_token is None or settings.alert_chat_id is None:
        typer.secho(
            "TELEGRAM_BOT_TOKEN and ALERT_CHAT_ID must both be set; see the "
            "bot section of src/itgraph/README.md.",
            fg=typer.colors.RED,
            err=True,
        )
        raise typer.Exit(1)

    token = settings.telegram_bot_token.get_secret_value()
    chat_id = settings.alert_chat_id

    # The bot's own connection when one is configured. Reported either
    # way: running under the collector's credentials is a supported
    # state, not a secret one, and silence about it would make the
    # hardened and unhardened deployments look identical.
    from sqlalchemy.engine import make_url

    url = str(settings.bot_database_url or settings.database_url)
    logging.getLogger("itgraph.bot").info(
        "connecting to the database as %r", make_url(url).username
    )

    async def run() -> None:
        database = Database(url)
        telegram = Bot(token=token)
        stop = asyncio.Event()
        dispatcher = build_dispatcher(database, chat_id)
        try:
            loop = asyncio.get_running_loop()
            for received in (signal.SIGINT, signal.SIGTERM):
                with suppress(NotImplementedError):
                    loop.add_signal_handler(received, stop.set)

            # Delivery and the handlers are separate concerns on one
            # connection: one pushes alerts out, the other takes the
            # operator's answers back. Neither may stall the other, so
            # they run as siblings rather than in sequence.
            delivery = asyncio.create_task(
                run_bot(database, BotSender(telegram, chat_id), stop=stop)
            )
            polling = asyncio.create_task(
                dispatcher.start_polling(telegram, handle_signals=False)
            )

            # Whichever of the two ends first ends the bot. Waiting only
            # on `stop` here let a dead sibling go unnoticed for hours:
            # see `bot.app.supervise` for what that looked like from the
            # outside.
            await supervise([delivery, polling], stop=stop)

            await dispatcher.stop_polling()
            with suppress(asyncio.CancelledError):
                polling.cancel()
                await polling

            # Asked rather than discarded. Either await re-raises what
            # killed that task, so the process exits non-zero and
            # `Restart=always` gets something to act on — with the
            # traceback in the journal instead of silence.
            if polling.done() and not polling.cancelled():
                polling.result()
            stats = await delivery
        finally:
            await telegram.session.close()
            await database.dispose()
        typer.echo(stats.line())

    _run(run())


@app.command()
def derive(
    rebuild: Annotated[
        bool,
        typer.Option(
            "--rebuild",
            help="Empty the derived tables first, then rebuild from scratch.",
        ),
    ] = False,
    batch_size: Annotated[
        int | None,
        typer.Option("--batch-size", help="Messages read per batch."),
    ] = None,
) -> None:
    """Derive the edge graph from the raw layer.

    Reads stored messages and writes forward and mention edges. Touches no
    network and is safe to re-run at any time: a pass over unchanged raw
    data writes nothing. Mention edges to a not-yet-known channel appear
    only after `itgraph resolve` turns the username into a channel and
    this command runs again.
    """
    from itgraph.db.session import Database
    from itgraph.derive.edges import derive_graph

    async def run() -> None:
        database = Database()
        try:
            summary = await derive_graph(
                database, batch_size=batch_size, rebuild=rebuild
            )
        finally:
            await database.dispose()
        typer.echo(summary.line())

    _run(run())


@app.command()
def resolve(
    tg_id: Annotated[
        int | None,
        typer.Argument(
            metavar="[TG_ID]",
            help=(
                "Resolve only this channel, by the id the inventory "
                "stores — the one `itgraph list` prints, without a "
                "`-100` prefix."
            ),
        ),
    ] = None,
    retry_failed: Annotated[
        bool,
        typer.Option(
            "--retry-failed",
            help="Also retry references a previous run could not resolve.",
        ),
    ] = False,
    limit: Annotated[
        int | None,
        typer.Option("--limit", help="Make at most this many requests."),
    ] = None,
    delay: Annotated[
        float | None,
        typer.Option("--delay", help="Seconds between requests."),
    ] = None,
    min_sources: Annotated[
        int | None,
        typer.Option(
            "--min-sources",
            help=(
                "Only resolve usernames mentioned by at least this many "
                "different channels."
            ),
        ),
    ] = None,
) -> None:
    """Fill in username and title for channels discovered by reference.

    The only command here that talks to Telegram, and it obeys the same
    pacing and FloodWait rules as `backfill`. It resolves channels found
    by forward (by id) and usernames left pending by a mention. Run
    `derive` again afterwards to write the mention edges the newly
    resolved channels unblock.

    The username queue is worked most-mentioned first. It is also the only
    place `contacts.resolveUsername` is spent — a couple of hundred a day,
    no batching — so `--min-sources 2` is how a day's budget goes to the
    references more than one channel thought worth making, rather than to
    whatever arrived first.

    Given a TG_ID, the run is that one channel: one request, and the
    username queue is not worked at all. The named channel still has to
    be in the resolution queue — one already resolved, or one a previous
    run failed on without `--retry-failed`, is refused before anything
    connects to Telegram.
    """
    from itgraph.db.channels import channel_to_resolve
    from itgraph.db.session import Database
    from itgraph.tg.client import connected
    from itgraph.tg.resolve import resolve_inventory

    if min_sources is not None and min_sources < 0:
        raise typer.BadParameter("--min-sources cannot be negative")
    if tg_id is not None and limit is not None:
        raise typer.BadParameter("--limit means nothing beside one TG_ID")
    if tg_id is not None and min_sources is not None:
        raise typer.BadParameter(
            "--min-sources orders the username queue, which a run for one "
            "TG_ID does not work"
        )

    async def run() -> None:
        database = Database()
        try:
            if tg_id is not None:
                # Before `connected`, deliberately: the session is held
                # under an exclusive lease, and a run that will refuse
                # must not be the reason `itgraph watch` cannot start.
                async with database.session() as session:
                    await channel_to_resolve(
                        session, tg_id, retry_failed=retry_failed
                    )
            async with connected("resolve") as client:
                summary = await resolve_inventory(
                    client,
                    database,
                    retry_failed=retry_failed,
                    delay=delay,
                    limit=limit,
                    min_sources=min_sources,
                    tg_id=tg_id,
                )
        finally:
            await database.dispose()
        typer.echo(summary.line())
        _report_halt(summary.halt)

    _run(run())


@app.command()
def backup(
    full: Annotated[
        bool,
        typer.Option("--full", help="Whole database, raw layer included."),
    ] = False,
    inventory: Annotated[
        bool,
        typer.Option("--inventory", help="Only the hand-reviewed tables."),
    ] = False,
) -> None:
    """Dump the database. With no options, takes whatever is due."""
    from itgraph.db.backup import (
        BackupError,
        full_kind,
        inventory_kind,
        run_backup,
    )

    if full and inventory:
        raise typer.BadParameter("give at most one of --full, --inventory")

    kinds = None
    if full:
        kinds = [full_kind()]
    elif inventory:
        kinds = [inventory_kind()]

    try:
        taken = run_backup(kinds=kinds)
    except BackupError as exc:
        typer.secho(str(exc), fg=typer.colors.RED, err=True)
        raise typer.Exit(1) from exc

    if not taken:
        typer.echo("nothing due")
    for item in taken:
        typer.echo(
            f"{item.kind:<10} {item.path} "
            f"({item.size:,} bytes, {item.entries} entries)"
        )


def _side(username: str | None, title: str | None, tg_id: int) -> str:
    """One half of a candidate pair, as much of it as fits."""
    handle = f"@{username}" if username else str(tg_id)
    return f"{handle} {(title or '-')[:28]}"


def _evidence(row: CandidateRow) -> str:
    """Why this pair was proposed, in one column per signal that fired.

    Printed rather than summarised into the score, because the score
    alone is not reviewable: the operator is being asked whether these
    two channels share an author, and the answer depends entirely on
    which signal said so.
    """
    parts = []
    if row.about_direction is not None:
        parts.append(f"about:{row.about_direction}")
    if row.shared_token is not None:
        parts.append(f"token:{row.shared_token}/{row.shared_token_channels}")
    if row.handle_token is not None:
        parts.append(f"handle:{row.handle_token}/{row.handle_token_channels}")
    if row.out_share is not None:
        source = "a" if row.out_share_src == row.channel_a else "b"
        parts.append(
            f"share:{source}={row.out_share:.2f} of {row.out_share_edges}"
        )
    if row.edges_a_to_b is not None:
        parts.append(f"mutual:{row.edges_a_to_b}/{row.edges_b_to_a}")
    return "  ".join(parts)


def _candidate_row(row: CandidateRow) -> str:
    """One pair on one line: both sides, their statuses, and the why."""
    return (
        f"{row.score:>6.3f}  "
        f"{_side(row.username_a, row.title_a, row.channel_a):<40}"
        f"{row.status_a.value:<10} "
        f"{_side(row.username_b, row.title_b, row.channel_b):<40}"
        f"{row.status_b.value:<10} {_evidence(row)}"
    )


def _candidate_lines(summary: DetectionSummary) -> list[str]:
    """The review list, with the pairs one handle proposed kept together.

    A family of five channels is nine pairs and **one** decision, so the
    pairs a named handle proposed are printed as a block rather than
    scattered down a list ordered by score. The block sits where its
    highest-scoring pair sits, so nothing outranks it that did not
    outrank that pair.

    Grouping is presentation only. `--limit` still bounds *pairs*, which
    is why a truncated block says how many of its own are missing rather
    than quietly showing part of a family as if it were the whole one.
    """
    lines: list[str] = []
    emitted: set[str] = set()
    for row in summary.rows:
        token = row.handle_token
        if token is None:
            lines.append(_candidate_row(row))
            continue
        if token in emitted:
            # Pulled up into the block at its first appearance.
            continue
        emitted.add(token)
        group = summary.groups[token]
        shown = [
            other for other in summary.rows if other.handle_token == token
        ]
        # "2 of 4 channels" where a filter has narrowed the group, so a
        # heading beside evidence reading `handle:atom/4` cannot look
        # like a miscount. Bare when nothing was narrowed.
        channels = (
            f"{len(group.members)} channels"
            if len(group.members) == group.carriers
            else f"{len(group.members)} of {group.carriers} channels"
        )
        pairs = "pair" if group.pairs == 1 else "pairs"
        head = f"@{token} — {channels}, {group.pairs} {pairs}"
        hidden = group.pairs - len(shown)
        if hidden:
            head += f", {hidden} not shown"
        lines.append(head)
        lines.extend(f"  {_candidate_row(other)}" for other in shown)
        # The confirmation, ready to run: retyping five usernames is
        # where a decision of this shape gets made wrong.
        lines.append(f"  itgraph family {' '.join(group.members)}")
    return lines


@app.command()
def affiliates(
    min_out_edges: Annotated[
        int,
        typer.Option(
            "--min-out-edges",
            help="Ignore concentration for channels with fewer out-edges.",
        ),
    ] = DEFAULT_THRESHOLDS.min_out_edges,
    max_share: Annotated[
        float,
        typer.Option(
            "--max-share",
            help="Share of one channel's out-edges to one target.",
        ),
    ] = DEFAULT_THRESHOLDS.max_share_min,
    min_token_length: Annotated[
        int,
        typer.Option("--min-token-length", help="Shortest username token."),
    ] = DEFAULT_THRESHOLDS.min_token_length,
    max_token_channels: Annotated[
        int,
        typer.Option(
            "--max-token-channels",
            help=(
                "A token on more channels than this is a subject, not an "
                "author."
            ),
        ),
    ] = DEFAULT_THRESHOLDS.max_token_channels,
    min_mutual_edges: Annotated[
        int,
        typer.Option(
            "--min-mutual-edges", help="Edges needed each way to count."
        ),
    ] = DEFAULT_THRESHOLDS.min_mutual_edges,
    max_handle_token_channels: Annotated[
        int,
        typer.Option(
            "--max-handle-token-channels",
            help="Most channels one signed handle may name.",
        ),
    ] = DEFAULT_THRESHOLDS.max_handle_token_channels,
    weight_about: Annotated[
        float, typer.Option("--weight-about", help="Weight of a description.")
    ] = DEFAULT_WEIGHTS.about,
    weight_handle: Annotated[
        float,
        typer.Option("--weight-handle", help="Weight of a signed handle."),
    ] = DEFAULT_WEIGHTS.handle,
    weight_share: Annotated[
        float, typer.Option("--weight-share", help="Weight of concentration.")
    ] = DEFAULT_WEIGHTS.share,
    weight_token: Annotated[
        float, typer.Option("--weight-token", help="Weight of a token.")
    ] = DEFAULT_WEIGHTS.token,
    weight_mutual: Annotated[
        float, typer.Option("--weight-mutual", help="Weight of mutuality.")
    ] = DEFAULT_WEIGHTS.mutual,
    edge_kind: Annotated[
        list[EdgeKind] | None,
        typer.Option("--edge-kind", help="Count only these edge kinds."),
    ] = None,
    limit: Annotated[
        int | None,
        typer.Option("--limit", help="Show at most this many pairs."),
    ] = None,
    show_decided: Annotated[
        bool,
        typer.Option("--all", help="Include pairs already decided."),
    ] = False,
    any_status: Annotated[
        bool,
        typer.Option(
            "--any-status",
            help="Also show pairs in which neither channel is a seed.",
        ),
    ] = False,
) -> None:
    """Find channels that may share an author, and rank them for review.

    Reads the inventory, the derived edges and the descriptions stored by
    `itgraph metadata`. Makes no network request, so it is free to re-run
    under different thresholds as often as you like — a re-run refreshes
    every score and evidence column and never touches a decision you have
    already made.

    It proposes and never decides: no run of this command writes a family
    link. Confirm a pair with `itgraph family`.

    Five signals, and they rarely agree with each other, so any one of
    them is enough to propose a pair and the weights mostly decide which
    list you read first.

    The pairs one signed handle proposed are printed as a block with the
    `itgraph family` line that would confirm them: an author's five
    channels are nine pairs and one decision.

    Only pairs with at least one seed in them are shown; the rest are
    still computed and stored, and `--any-status` shows them. Nothing is
    lost by the default — edges and descriptions exist only for channels
    the collector walked, and it walks seeds, so three of the four
    signals always have a seed on one side anyway.
    """
    from itgraph.affiliation.detect import InvalidParameterError
    from itgraph.affiliation.run import run_detection
    from itgraph.affiliation.signals import Thresholds, Weights
    from itgraph.db.session import Database

    thresholds = Thresholds(
        min_out_edges=min_out_edges,
        max_share_min=max_share,
        min_token_length=min_token_length,
        max_token_channels=max_token_channels,
        min_mutual_edges=min_mutual_edges,
        max_handle_token_channels=max_handle_token_channels,
    )
    weights = Weights(
        about=weight_about,
        handle=weight_handle,
        share=weight_share,
        token=weight_token,
        mutual=weight_mutual,
    )
    kinds = list(edge_kind) if edge_kind else list(EdgeKind)
    if limit is not None and limit < 1:
        raise typer.BadParameter("--limit must be at least 1")

    async def run() -> None:
        database = Database()
        try:
            summary = await run_detection(
                database,
                thresholds=thresholds,
                weights=weights,
                edge_kinds=kinds,
                limit=limit,
                include_decided=show_decided,
                seeds_only=not any_status,
            )
        finally:
            await database.dispose()

        for line in summary.coverage_lines():
            typer.echo(line)
        typer.echo(summary.line())
        if not summary.rows:
            return
        typer.echo("")
        for line in _candidate_lines(summary):
            typer.echo(line)

    try:
        _run(run())
    except InvalidParameterError as exc:
        raise typer.BadParameter(str(exc)) from exc


@app.command()
def family(
    channel_refs: Annotated[
        list[str],
        typer.Argument(
            metavar="CHANNEL...",
            help="The channels, by Telegram id or @username.",
        ),
    ],
    reject: Annotated[
        bool,
        typer.Option("--reject", help="Record that they are not affiliated."),
    ] = False,
    withdraw: Annotated[
        bool,
        typer.Option(
            "--withdraw", help="Undo a decision and reopen the pair."
        ),
    ] = False,
    note: Annotated[
        str | None,
        typer.Option(
            "--note", help="Free-text note stored with the decision."
        ),
    ] = None,
) -> None:
    """Record that channels share an author — or that two do not.

    This is the only command that records a family. `itgraph affiliates`
    proposes pairs and ranks them; nothing it finds becomes a fact until
    it is confirmed here.

    A family is a set with no main channel. Name two channels or twenty —
    the statement is the same, and every pair among them is recorded. The
    order does not matter, and neither does which pairs detection
    happened to find: confirming a pair that bridges two families merges
    them, which is what saying "these share an author" means.

    A rejection takes exactly two channels — it is a statement about a
    pair — and it is stored so that pair stops being proposed at every
    subsequent run. `--withdraw` undoes either; a withdrawn pair splits
    its family only if nothing else was connecting the two sides.

    Channels no signal ever paired can be confirmed directly — you may
    simply know. Those pairs are recorded as having come from you rather
    than from a signal.
    """
    from itgraph.db.channels import (
        confirm_affiliation,
        reject_affiliation,
        withdraw_affiliation,
    )
    from itgraph.db.session import Database

    if reject and withdraw:
        raise typer.BadParameter("give at most one of --reject, --withdraw")
    if len(channel_refs) < 2:
        raise typer.BadParameter("name at least two channels")
    if (reject or withdraw) and len(channel_refs) != 2:
        # Rejecting or withdrawing says something about one pair. A group
        # form would have to invent which pairs it meant.
        raise typer.BadParameter(
            "--reject and --withdraw take exactly two channels"
        )

    refs = [_channel_ref(ref) for ref in channel_refs]

    async def run() -> None:
        database = Database()
        try:
            async with database.session() as session:
                if withdraw:
                    pair = await withdraw_affiliation(session, *refs)
                    typer.echo(f"{pair[0]} and {pair[1]}: decision withdrawn")
                elif reject:
                    pair = await reject_affiliation(session, *refs, note=note)
                    typer.echo(f"{pair[0]} and {pair[1]}: not affiliated")
                else:
                    group = await confirm_affiliation(session, refs, note=note)
                    # The family can be larger than what was named, when a
                    # pair bridged two of them. Saying so is what keeps a
                    # merge from being silent.
                    typer.echo(
                        f"{group.pairs} pairs recorded; "
                        f"family of {group.channels} channels"
                    )
        finally:
            await database.dispose()

    try:
        _run(run())
    except ValueError as exc:
        typer.secho(str(exc), fg=typer.colors.RED, err=True)
        raise typer.Exit(1) from exc


def _row(channel: Channel) -> str:
    """One inventory line: id, username, title, status, kind."""
    username = f"@{channel.username}" if channel.username else "-"
    title = (channel.title or "-")[:40]
    kind = channel.kind.value if channel.kind else "-"
    return (
        f"{channel.tg_id:<15} {username:<24} {title:<42} "
        f"{channel.status.value:<10} {kind}"
    )


def _backfill_column(state: BackfillState | None) -> str:
    """How far the collector got, in one column.

    A run spans hours and several sittings, so the question "what is left
    to do" has to be answerable without opening psql.
    """
    if state is None:
        return "-"
    if state.failure_kind is not None:
        return f"{state.status.value}/{state.failure_kind.value}"
    if state.status.value == "complete" and state.cutoff_at is not None:
        return f"complete to {state.cutoff_at:%Y-%m-%d}"
    return state.status.value


@app.command()
def channels(
    status: Annotated[
        ChannelStatus | None,
        typer.Option("--status", help="Show only this status."),
    ] = None,
    family_ref: Annotated[
        str | None,
        typer.Option(
            "--family",
            metavar="CHANNEL",
            help="Show one family: any channel in it, by id or @username.",
        ),
    ] = None,
    backfill_state: Annotated[
        bool,
        typer.Option("--backfill", help="Show how far collection got."),
    ] = False,
) -> None:
    """List the inventory, or summarise review progress."""
    from itgraph.db.affiliation import family_keys, family_of
    from itgraph.db.channels import (
        count_by_status,
        count_families,
        find_channel,
        list_channels,
    )
    from itgraph.db.session import Database

    async def run() -> None:
        database = Database()
        try:
            async with database.session() as session:
                family_key: int | None = None
                if family_ref is not None:
                    named = await find_channel(
                        session, _channel_ref(family_ref)
                    )
                    # Any member names the family, and the answer is the
                    # same set whichever one is given — there is no main
                    # channel to have to know.
                    family_key = family_of(
                        await family_keys(session), named.tg_id
                    )

                if status is None and family_key is None:
                    counts = await count_by_status(session)
                    for name, total in counts.items():
                        typer.echo(f"{name.value:<10} {total}")
                    families = await count_families(session)
                    typer.echo(
                        f"{'families':<10} {families.families} "
                        f"({families.channels} channels)"
                    )
                    typer.echo("")

                for channel in await list_channels(
                    session, status=status, family=family_key
                ):
                    line = _row(channel)
                    if backfill_state:
                        state = await session.get(BackfillState, channel.tg_id)
                        line = f"{line:<100} {_backfill_column(state)}"
                    typer.echo(line)
        finally:
            await database.dispose()

    _run(run())


@app.command()
def floods(
    since: Annotated[
        datetime | None,
        typer.Option(
            "--since",
            formats=["%Y-%m-%d"],
            help="Only rate limits recorded on or after this date.",
        ),
    ] = None,
    limit: Annotated[
        int,
        typer.Option("--limit", help="Show at most this many events."),
    ] = 50,
) -> None:
    """Show the rate limits collection has run into, and what caused them.

    `backfill` and `resolve` both spend `contacts.resolveUsername` and
    `channels.getChannels`, so the method is what says whether a limit
    one of them hit also applies to the other. That question is the
    reason this record exists.
    """
    from itgraph.db.floods import flood_summary, recent_floods
    from itgraph.db.session import Database

    cutoff = since.replace(tzinfo=UTC) if since is not None else None

    async def run() -> None:
        database = Database()
        try:
            async with database.session() as session:
                events = await recent_floods(
                    session, since=cutoff, limit=limit
                )
                if not events:
                    typer.echo(
                        "No rate limits recorded"
                        + (" in that window." if cutoff else " yet.")
                    )
                    return

                tallies = await flood_summary(session, since=cutoff)
                typer.echo(f"{'method':<34} {'times':>5} {'longest':>9}")
                for tally in tallies:
                    typer.echo(
                        f"{tally.method:<34} {tally.times:>5} "
                        f"{tally.longest:>8}s"
                    )

                typer.echo("")
                for event in events:
                    channel = event.channel_id or "-"
                    halted = " HALTED" if event.halted else ""
                    typer.echo(
                        f"{event.occurred_at:%Y-%m-%d %H:%M} "
                        f"{event.command.value:<8} "
                        f"{event.method:<34} "
                        f"{event.seconds:>6}s  {channel}{halted}"
                    )

                typer.echo(
                    "\nA row is not proof a request was sent: Telethon "
                    "refuses a method that is\nstill under a wait, and "
                    "that refusal arrives as a rate limit too. Waits "
                    "shorter\nthan flood_sleep_threshold never reach "
                    "this record at all."
                )
        finally:
            await database.dispose()

    _run(run())


# --- YouTube ------------------------------------------------------------
#
# A sub-app of its own, so `itgraph --help` keeps listing Telegram's
# commands and `itgraph yt --help` lists YouTube's. Only `add` and
# `refresh` make requests; everything else reads the database alone.

yt_app = typer.Typer(
    no_args_is_help=True,
    help="Inventory and review of YouTube channels.",
)
app.add_typer(yt_app, name="yt")


async def _expire_youtube_data() -> None:
    """Delete every YouTube payload past its 30 days.

    Needs no key and no network: a payload past its 30 days is a
    violation whether or not anything was going to read it.
    """
    from itgraph.db.session import Database
    from itgraph.db.yt_channels import purge_expired

    database = Database()
    try:
        async with database.session() as session:
            removed = await purge_expired(session)
    finally:
        await database.dispose()
    if removed:
        logging.getLogger(__name__).info(
            "removed %d YouTube payloads older than 30 days", removed
        )


def _run_yt(body: Coroutine[Any, Any, None]) -> None:
    """``_run`` for the YouTube commands: expiry first, then the command,
    and its expected failures as sentences on stderr and exit 1.

    Expiry lives here, not in each command, so a command added later
    cannot forget it — and not in the sub-app's callback, which click
    runs before a subcommand's ``--help`` is handled: there it made help
    need a database and a `.env`.

    A spent quota is an ordinary thing to discover, and so is a channel
    nobody has added yet; neither deserves a traceback.
    """
    from itgraph.db.yt_channels import YtChannelLookupError
    from itgraph.db.yt_quota import QuotaError
    from itgraph.yt.errors import YtError
    from itgraph.yt_review import ReviewFileError, UnreadableReviewFile

    async def expired_first() -> None:
        await _expire_youtube_data()
        await body

    try:
        asyncio.run(expired_first())
    except (
        QuotaError,
        YtError,
        YtChannelLookupError,
        ReviewFileError,
        UnreadableReviewFile,
    ) as exc:
        typer.secho(str(exc), fg=typer.colors.RED, err=True)
        raise typer.Exit(1) from exc


@yt_app.command("quota")
def yt_quota() -> None:
    """Show today's YouTube quota: per pool, per command, and the reset.

    Makes no request. The day is Google's — the Pacific date — so the
    reset is printed in UTC and on this machine's clock.
    """
    from itgraph.db.session import Database
    from itgraph.db.yt_quota import describe_reset, quota_report

    async def run() -> None:
        database = Database()
        try:
            async with database.session() as session:
                report = await quota_report(session)
        finally:
            await database.dispose()

        typer.echo(
            f"quota day {report.day} (Pacific), next reset "
            f"{describe_reset(report.reset)}"
        )
        for use in report.pools:
            typer.echo(
                f"{use.pool.value:<10} {use.spent:>6} of {use.cap:>6} units, "
                f"{max(use.cap - use.spent, 0)} left"
            )
        if report.exhausted is not None:
            typer.secho(
                f"exhausted: YouTube answered quotaExceeded at "
                f"{report.exhausted.observed_at:%H:%M} UTC, during "
                f"`yt {report.exhausted.command.value}`; nothing is sent "
                "until the reset",
                fg=typer.colors.RED,
            )
        if not report.methods:
            typer.echo("No requests today.")
            return

        typer.echo("")
        typer.echo(
            f"{'command':<10} {'method':<16} {'requests':>8} {'units':>6} "
            f"{'unknown':>7}"
        )
        for method in report.methods:
            typer.echo(
                f"{method.command.value:<10} {method.method:<16} "
                f"{method.requests:>8} {method.units:>6} {method.unknown:>7}"
            )
        if any(method.unknown for method in report.methods):
            typer.echo(
                "\n`unknown` is a request booked and never answered — the "
                "process died or the\nconnection broke. Its units stay "
                "counted: YouTube may well have charged them."
            )

    _run_yt(run())


@yt_app.command("refresh")
def yt_refresh() -> None:
    """Fetch again the channel data that is about to expire.

    What YouTube says about a channel may be kept 30 days. Anything older
    than the refresh age (25 days by default) is asked for again, fifty
    channels a unit, whatever the channel's status. A channel YouTube no
    longer returns loses its data and is marked gone; its record and
    review stay. Runs daily from a timer on the host.
    """
    from itgraph.db.models import YtCommand
    from itgraph.db.session import Database
    from itgraph.db.yt_quota import check_not_exhausted
    from itgraph.yt.client import youtube
    from itgraph.yt.refresh import refresh_channels

    async def run() -> None:
        database = Database()
        try:
            await check_not_exhausted(database)
            async with youtube(database, YtCommand.REFRESH) as client:
                summary = await refresh_channels(client, database)
        finally:
            await database.dispose()

        typer.echo(summary.line())
        for channel_id in summary.gone:
            typer.echo(f"  gone: {channel_id}")
        for channel_id in summary.returned:
            typer.echo(f"  back: {channel_id}")
        if summary.stopped is not None:
            typer.secho(str(summary.stopped), fg=typer.colors.RED, err=True)
            raise typer.Exit(1)

    _run_yt(run())


def _read_input(source: str) -> str:
    """A file's text, or standard input's when the source is ``-``."""
    import sys

    if source == "-":
        return sys.stdin.read()
    path = Path(source)
    if not path.is_file():
        raise typer.BadParameter(f"no such file: {source}")
    return path.read_text(encoding="utf-8")


@yt_app.command("add")
def yt_add(
    entries: Annotated[
        list[str] | None,
        typer.Argument(
            metavar="[CHANNEL]...",
            help="@handle, handle, a channel link, or a UC… id.",
        ),
    ] = None,
    from_file: Annotated[
        str | None,
        typer.Option(
            "--from-file",
            metavar="PATH|-",
            help="Read channels from a file, one per line; - for stdin.",
        ),
    ] = None,
    limit: Annotated[
        int | None,
        typer.Option("--limit", help="Make at most this many requests."),
    ] = None,
    failures: Annotated[
        str | None,
        typer.Option(
            "--failures",
            metavar="PATH|-",
            help="Write the channels not found, as the next run's input; "
            "- for stdout.",
        ),
    ] = None,
) -> None:
    """Add YouTube channels to the inventory, resolving each through the API.

    Every entry becomes a channel id before a record exists; one YouTube
    returns nothing for creates nothing. Forms the API cannot look up — a
    /c/ link, a bare youtube.com/NAME, a video — are refused, all of them
    at once, before anything is spent.

    An entry the inventory already answers costs no request, so the same
    input continues a run that stopped. Ids go fifty to a unit, handles one
    each; `--limit` counts requests. Records are created unreviewed: review
    them with `itgraph yt review-export` once you have seen what each entry
    resolved to.

    With `--failures -` the failures go to stdout and the report to
    stderr, so the two can be separated over SSH.
    """
    from itgraph.db.models import YtCommand
    from itgraph.db.session import Database
    from itgraph.db.yt_quota import check_not_exhausted
    from itgraph.yt.add import add_channels
    from itgraph.yt.client import youtube
    from itgraph.yt_entries import EntryError, parse_entries, read_entries

    if bool(entries) == bool(from_file):
        raise typer.BadParameter(
            "give channels or --from-file, not both and not neither"
        )
    if limit is not None and limit < 1:
        raise typer.BadParameter("--limit must be at least 1")

    try:
        parsed = (
            read_entries(_read_input(from_file))
            if from_file is not None
            else parse_entries(entries or [])
        )
    except EntryError as exc:
        raise typer.BadParameter(str(exc)) from exc
    if not parsed:
        typer.echo("Nothing to add.")
        return

    # The report goes wherever the failures do not.
    report_to_stderr = failures == "-"

    def say(line: str) -> None:
        typer.echo(line, err=report_to_stderr)

    async def run() -> None:
        database = Database()
        try:
            await check_not_exhausted(database)
            async with youtube(database, YtCommand.ADD, limit=limit) as client:
                summary = await add_channels(client, database, parsed)
        finally:
            await database.dispose()

        say(summary.line())
        for entry, resource in summary.added:
            snippet = resource.get("snippet", {})
            say(
                f"  + {resource['id']}  {snippet.get('customUrl', '-')}  "
                f"{snippet.get('title', '-')}"
            )
        for entry, first in summary.same:
            say(
                f"  = {entry.where} {entry.text!r}: same channel as "
                f"{first.where} {first.text!r}"
            )
        for entry in summary.not_found:
            say(f"  ? {entry.where} {entry.text!r}: not found")
        if summary.unreached:
            say(f"{len(summary.unreached)} not reached")

        lost = summary.failures()
        if failures is not None and lost:
            text = "".join(
                f"{entry.text}  # {reason}\n" for entry, reason in lost
            )
            if failures == "-":
                typer.echo(text, nl=False)
            else:
                Path(failures).write_text(text, encoding="utf-8")
                say(f"failures written to {failures}")

        if summary.limited is not None:
            say(str(summary.limited))
        if summary.stopped is not None:
            typer.secho(str(summary.stopped), fg=typer.colors.RED, err=True)
            raise typer.Exit(1)

    _run_yt(run())


@yt_app.command("review-export")
def yt_review_export(
    status: Annotated[
        ChannelStatus | None,
        typer.Option(
            "--status",
            help="Export channels with this status instead of the "
            "unreviewed ones.",
        ),
    ] = None,
    output: Annotated[
        Path | None,
        typer.Option(
            "--output",
            dir_okay=False,
            help="Write here instead of to stdout.",
        ),
    ] = None,
) -> None:
    """Write a review file: the channels awaiting review, one per line.

    Tab-separated, for a text editor: decision, kind, lang, reason and
    note to fill in, then the handle, title, subscribers and a link to
    look at. Makes no request. The file holds YouTube API data, so it
    expires 30 days after export and is refused after that; delete it
    once applied, and never commit it.

    From the operator's machine:
    `scripts/on-host.sh yt review-export > ~/itgraph-review/batch.review.tsv`
    """
    from itgraph.db.session import Database
    from itgraph.db.yt_channels import list_channels
    from itgraph.yt_review import export_row, render_review

    async def run() -> None:
        database = Database()
        try:
            async with database.session() as session:
                listed = await list_channels(
                    session, status=status, unreviewed=status is None
                )
        finally:
            await database.dispose()

        text = render_review(
            (export_row(item.channel, item.payload) for item in listed),
            exported_at=datetime.now(UTC),
        )
        if output is None:
            typer.echo(text, nl=False)
        else:
            output.write_text(text, encoding="utf-8")
        typer.echo(f"{len(listed)} channels exported", err=True)

    _run_yt(run())


@yt_app.command("review-apply")
def yt_review_apply(
    source: Annotated[
        str,
        typer.Argument(
            metavar="PATH|-", help="The edited review file; - for stdin."
        ),
    ],
) -> None:
    """Apply an edited review file, all of it or none of it.

    Every mistake — a seed without a kind or a language, a rejection
    without a reason, a misspelt value, an unknown channel, a channel
    listed twice — is reported at once with its line number, and nothing
    is written while any is left. A row left blank is skipped. A channel
    whose review changed after the file was exported is a conflict and is
    not written; change a decision already made with `itgraph yt mark`.
    Applying the same file twice changes nothing. Makes no request.
    """
    from itgraph.db.session import Database
    from itgraph.db.yt_channels import apply_review
    from itgraph.yt_review import parse_review

    text = _read_input(source)

    async def run() -> None:
        parsed = parse_review(text, now=datetime.now(UTC))
        database = Database()
        try:
            async with database.session() as session:
                summary = await apply_review(session, parsed)
        finally:
            await database.dispose()

        typer.echo(summary.line())
        for decision in summary.conflicts:
            typer.echo(
                f"  ! {decision.where} {decision.channel_id}: reviewed since "
                "the export — not written; use `itgraph yt mark` to change it"
            )

    _run_yt(run())


@yt_app.command("mark")
def yt_mark(
    channel_ref: Annotated[
        str,
        typer.Argument(metavar="CHANNEL", help="UC… id or @handle."),
    ],
    seed: Annotated[
        bool, typer.Option("--seed", help="In scope: collect from it.")
    ] = False,
    maybe: Annotated[
        bool, typer.Option("--maybe", help="Undecided; look again later.")
    ] = False,
    reject: Annotated[
        bool, typer.Option("--reject", help="Out of scope.")
    ] = False,
    kind: Annotated[
        YtChannelKind | None,
        typer.Option(
            "--kind", help="What the channel is. Required with --seed."
        ),
    ] = None,
    lang: Annotated[
        YtLang | None,
        typer.Option("--lang", help="Its language. Required with --seed."),
    ] = None,
    reason: Annotated[
        YtRejectReason | None,
        typer.Option("--reason", help="Required with --reject."),
    ] = None,
    note: Annotated[
        str | None,
        typer.Option(
            "--note", help="Free text beside the kind or the reason."
        ),
    ] = None,
) -> None:
    """Record one review decision, replacing any made before.

    The way to change a decision already made: a review file never
    overwrites a decision it did not see. A kind, language or note left
    out keeps its current value; a rejection without --note has none.
    Makes no request.
    """
    from itgraph.db.session import Database
    from itgraph.db.yt_channels import Review, mark_channel

    if sum((seed, maybe, reject)) != 1:
        raise typer.BadParameter(
            "give exactly one of --seed, --maybe, --reject"
        )
    if seed and (kind is None or lang is None):
        raise typer.BadParameter("--seed needs --kind and --lang")
    if reject and reason is None:
        raise typer.BadParameter("--reject needs --reason")
    if reason is not None and not reject:
        raise typer.BadParameter("--reason only goes with --reject")

    if seed:
        status = ChannelStatus.SEED
    elif maybe:
        status = ChannelStatus.MAYBE
    else:
        status = ChannelStatus.REJECTED
    review = Review(
        status=status, kind=kind, lang=lang, reason=reason, note=note
    )

    async def run() -> None:
        database = Database()
        try:
            async with database.session() as session:
                channel = await mark_channel(session, channel_ref, review)
                typer.echo(f"{channel.channel_id} -> {channel.status.value}")
        finally:
            await database.dispose()

    _run_yt(run())


def _yt_row(channel: YtChannel, payload: Mapping[str, Any] | None) -> str:
    """One inventory line: id, handle, title, status, kind, language."""
    if payload is not None:
        snippet = payload.get("snippet") or {}
        handle = str(snippet.get("customUrl") or "-")
        title = str(snippet.get("title") or "-")[:40]
    else:
        # Said, not left blank: an empty title reads as a channel without
        # one, and these have one YouTube may no longer be asked about.
        handle = "(gone)" if channel.gone_at is not None else "(expired)"
        title = "-"
    kind = channel.kind.value if channel.kind else "-"
    lang = channel.lang.value if channel.lang else "-"
    return (
        f"{channel.channel_id:<25} {handle:<24} {title:<42} "
        f"{channel.status.value:<10} {kind:<10} {lang}"
    )


@yt_app.command("channels")
def yt_channels(
    status: Annotated[
        ChannelStatus | None,
        typer.Option("--status", help="Show only this status."),
    ] = None,
) -> None:
    """List the YouTube inventory, or summarise review progress.

    Without a filter, the counts come first: per status, seeds per
    language, and how many channels have current data, expired data, or
    are no longer returned by YouTube. Makes no request.
    """
    from itgraph.db.session import Database
    from itgraph.db.yt_channels import inventory_summary, list_channels

    async def run() -> None:
        database = Database()
        try:
            async with database.session() as session:
                if status is None:
                    summary = await inventory_summary(session)
                    for name, total in summary.by_status.items():
                        typer.echo(f"{name.value:<10} {total}")
                    seeds = ", ".join(
                        f"{lang.value} {total}"
                        for lang, total in summary.seeds_by_lang.items()
                    )
                    typer.echo(f"{'seeds':<10} {seeds}")
                    typer.echo(
                        f"{'data':<10} current {summary.current}, expired "
                        f"{summary.expired}, gone {summary.gone}"
                    )
                    typer.echo("")
                for item in await list_channels(session, status=status):
                    typer.echo(_yt_row(item.channel, item.payload))
        finally:
            await database.dispose()

    _run_yt(run())
