"""Typer entrypoint. Commands parse arguments and delegate; no logic here.

Imports of anything that reads the environment stay inside the command
bodies, so `itgraph --help` works on a machine with no `.env` yet.
"""

import asyncio
import logging
from collections.abc import Coroutine
from typing import Annotated, Any

import typer

from itgraph import __version__
from itgraph.db.models import (
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
    from itgraph.db.channels import ChannelNotFoundError
    from itgraph.tg.client import NotAuthorizedError

    try:
        asyncio.run(body)
    except (ChannelNotFoundError, NotAuthorizedError) as exc:
        typer.secho(str(exc), fg=typer.colors.RED, err=True)
        raise typer.Exit(1) from exc


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
def mark(
    tg_id: Annotated[
        int, typer.Argument(help="Telegram id of the channel to review.")
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
                    tg_id,
                    status=status,
                    kind=kind,
                    reject_reason=reason,
                    reject_note=note,
                )
                typer.echo(f"{channel.tg_id} -> {channel.status.value}")
        finally:
            await database.dispose()

    _run(run())


def _row(channel: Channel) -> str:
    """One inventory line: id, username, title, status, kind."""
    username = f"@{channel.username}" if channel.username else "-"
    title = (channel.title or "-")[:40]
    kind = channel.kind.value if channel.kind else "-"
    return (
        f"{channel.tg_id:<15} {username:<24} {title:<42} "
        f"{channel.status.value:<10} {kind}"
    )


@app.command()
def channels(
    status: Annotated[
        ChannelStatus | None,
        typer.Option("--status", help="Show only this status."),
    ] = None,
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
                    typer.echo(_row(channel))
        finally:
            await database.dispose()

    _run(run())
