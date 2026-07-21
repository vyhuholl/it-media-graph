"""Typer entrypoint. Commands parse arguments and delegate; no logic here."""

import asyncio
import logging
from typing import Annotated

import typer

from itgraph import __version__

app = typer.Typer(
    no_args_is_help=True,
    help="Collect and store the IT-media Telegram graph.",
)


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
def login() -> None:
    """Authorize the MTProto session (asks for phone number and code)."""
    from itgraph.tg.client import build_client

    async def run() -> None:
        client = build_client()
        await client.start()
        try:
            me = await client.get_me()
            typer.echo(f"Authorized as {getattr(me, 'username', None) or me}")
        finally:
            await client.disconnect()

    asyncio.run(run())
