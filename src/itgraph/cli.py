"""Typer entrypoint. Commands parse arguments and delegate; no logic here.

Imports of anything that reads the environment stay inside the command
bodies, so `itgraph --help` works on a machine with no `.env` yet.
"""

import asyncio
import logging
from collections.abc import Coroutine
from datetime import UTC, datetime
from pathlib import Path
from typing import TYPE_CHECKING, Annotated, Any

import typer

if TYPE_CHECKING:
    # Under `TYPE_CHECKING` because importing it for real would pull in
    # `settings`, and `itgraph --help` has to work before there is a
    # `.env` to read.
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
            async with connected() as client:
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
    if row.out_share is not None:
        source = "a" if row.out_share_src == row.channel_a else "b"
        parts.append(
            f"share:{source}={row.out_share:.2f} of {row.out_share_edges}"
        )
    if row.edges_a_to_b is not None:
        parts.append(f"mutual:{row.edges_a_to_b}/{row.edges_b_to_a}")
    return "  ".join(parts)


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
    weight_about: Annotated[
        float, typer.Option("--weight-about", help="Weight of a description.")
    ] = DEFAULT_WEIGHTS.about,
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

    Four signals, and they rarely agree with each other, so any one of
    them is enough to propose a pair and the weights mostly decide which
    list you read first.

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
    )
    weights = Weights(
        about=weight_about,
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
        for row in summary.rows:
            typer.echo(
                f"{row.score:>6.3f}  "
                f"{_side(row.username_a, row.title_a, row.channel_a):<40}"
                f"{row.status_a.value:<10} "
                f"{_side(row.username_b, row.title_b, row.channel_b):<40}"
                f"{row.status_b.value:<10} {_evidence(row)}"
            )

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
            help="The two channels, by Telegram id or @username.",
        ),
    ],
    canonical: Annotated[
        str | None,
        typer.Option(
            "--canonical",
            help="Which of the two is the family's main channel.",
        ),
    ] = None,
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
    """Record that two channels share an author — or that they do not.

    This is the only command that writes a family link. `itgraph
    affiliates` proposes pairs and ranks them; nothing it finds becomes a
    fact until it is confirmed here.

    A confirmation needs `--canonical`, naming which of the two is the
    family's main channel. The other one points at it, the same shape a
    discussion chat points at its parent, and the family of any channel
    is then the one it names or itself.

    A rejection is stored too, and that is the point of storing it: it
    stops the pair being proposed at every subsequent run. Use
    `--withdraw` to undo either.

    A pair no signal proposed can be confirmed directly — you may simply
    know. It is recorded as having come from you rather than from a
    signal.
    """
    from itgraph.db.channels import (
        confirm_affiliation,
        recanonicalize_family,
        reject_affiliation,
        withdraw_affiliation,
    )
    from itgraph.db.session import Database

    if reject and withdraw:
        raise typer.BadParameter("give at most one of --reject, --withdraw")
    if reject and canonical is not None:
        raise typer.BadParameter("a rejection has no canonical channel")

    # One channel with `--canonical` is the re-canonicalize form: it
    # names the member to promote and needs no second reference.
    promoting = len(channel_refs) == 1
    if promoting:
        if reject or withdraw or note is not None:
            raise typer.BadParameter(
                "promoting one channel to canonical takes no other options"
            )
        # The single argument is the channel being promoted, so a
        # `--canonical` naming a different one contradicts it. Refusing
        # beats picking a winner: silently promoting the argument would
        # do the opposite of what the flag says.
        if canonical is not None and _channel_ref(canonical) != _channel_ref(
            channel_refs[0]
        ):
            raise typer.BadParameter(
                f"promoting {channel_refs[0]} but --canonical names "
                f"{canonical}; give one channel, or two to confirm a pair"
            )
    elif len(channel_refs) != 2:
        raise typer.BadParameter(
            "give two channels, or one with --canonical to promote it"
        )
    elif not reject and not withdraw and canonical is None:
        raise typer.BadParameter(
            "--canonical must name which of the two is the main channel"
        )

    async def run() -> None:
        database = Database()
        try:
            async with database.session() as session:
                if promoting:
                    counts = await recanonicalize_family(
                        session, _channel_ref(channel_refs[0])
                    )
                    typer.echo(
                        f"{channel_refs[0]} is now canonical for "
                        f"{counts.channels} channels"
                    )
                    return

                first, second = (_channel_ref(ref) for ref in channel_refs)
                if withdraw:
                    pair = await withdraw_affiliation(session, first, second)
                    typer.echo(f"{pair[0]} and {pair[1]}: decision withdrawn")
                elif reject:
                    pair = await reject_affiliation(
                        session, first, second, note=note
                    )
                    typer.echo(f"{pair[0]} and {pair[1]}: not affiliated")
                else:
                    assert canonical is not None
                    link = await confirm_affiliation(
                        session,
                        first,
                        second,
                        canonical=_channel_ref(canonical),
                        note=note,
                    )
                    typer.echo(
                        f"{link.member} now belongs to the family of "
                        f"{link.canonical}"
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
                    # Any member names the family; the key is the same
                    # expression the analysis uses.
                    family_key = named.operator_id or named.tg_id

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
                    if family_key is not None:
                        line = f"{line:<100} " + (
                            "member" if channel.operator_id else "canonical"
                        )
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
