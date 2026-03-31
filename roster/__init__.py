"""Roster — Manage parallel AI agent development runs."""

import sys

from .cli import app, _format_error


def main() -> None:
    """Entry point. Runs the Typer app with clean error handling."""
    try:
        app(standalone_mode=False)
    except SystemExit:
        raise  # Let typer.Exit(0) pass through cleanly
    except KeyboardInterrupt:
        from rich.console import Console

        Console().print("\n[dim]Cancelled.[/dim]")
        sys.exit(1)
    except Exception as e:
        _format_error(e)
        sys.exit(1)
