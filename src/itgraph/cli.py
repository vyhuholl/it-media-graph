"""Typer entrypoint. Commands parse arguments and delegate; no logic here.

Imports of anything that reads the environment stay inside the command
bodies, so `itgraph --help` works on a machine with no `.env` yet.
"""

import asyncio
import logging
from collections.abc import Coroutine
from datetime import UTC, datetime
from typing import TYPE_CHECKING, Annotated, Any

import typer

if TYPE_CHECKING:
    # Under `TYPE_CHECKING` because importing it for real would pull in
    # `settings`, and `itgraph --help` has to work before there is a
    # `.env` to read.
    from itgraph.tg.backfill import FloodWaitTooLong

from itgraph import __version__
from itgraph.db.models import (
    BackfillState,
    Channel,
    ChannelKind,
    ChannelStatus,
    RejectReason,
)

app = typer.Typer(
    no_args_is_help=True,
    help="Collect and store the IT-media Telegram graph.",
)


def _run(body: Coroutine[Any, Any, None]) -> None:
    """Run a command body, turning an expected failure into exit 1."""
    from itgraph.db.channels import ChannelLookupError
    from itgraph.tg.client import NotAuthorizedError

    try:
        asyncio.run(body)
    except (ChannelLookupError, NotAuthorizedError) as exc:
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

    from itgraph.tg.auth import authorize_qr
    from itgraph.tg.client import build_client

    async def run() -> None:
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
            typer.echo(f"Authorized as {getattr(me, 'username', None) or me}")
        finally:
            await client.disconnect()

    asyncio.run(run())


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
                connected() as client,
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
                connected() as client,
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
            async with connected() as client:
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
    """
    from itgraph.db.session import Database
    from itgraph.tg.client import connected
    from itgraph.tg.resolve import resolve_inventory

    if min_sources is not None and min_sources < 0:
        raise typer.BadParameter("--min-sources cannot be negative")

    async def run() -> None:
        database = Database()
        try:
            async with connected() as client:
                summary = await resolve_inventory(
                    client,
                    database,
                    retry_failed=retry_failed,
                    delay=delay,
                    limit=limit,
                    min_sources=min_sources,
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
    backfill_state: Annotated[
        bool,
        typer.Option("--backfill", help="Show how far collection got."),
    ] = False,
) -> None:
    """List the inventory, or summarise review progress."""
    from itgraph.db.channels import count_by_status, list_channels
    from itgraph.db.session import Database

    async def run() -> None:
        database = Database()
        try:
            async with database.session() as session:
                if status is None:
                    counts = await count_by_status(session)
                    for name, total in counts.items():
                        typer.echo(f"{name.value:<10} {total}")
                    typer.echo("")
                for channel in await list_channels(session, status=status):
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
