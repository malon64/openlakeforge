"""`olf init` -- initialize a writable OpenLakeForge project."""

from __future__ import annotations

import typer

from olf.commands._shared import fail
from olf.initialization import InitializationError, initialize_project


def initialize(
    empty: bool = typer.Option(False, "--empty", help="Create a transitional empty project instead of the demo."),
) -> None:
    """Create a writable lakehouse project in the current directory."""
    try:
        result = initialize_project(empty=empty)
    except InitializationError as exc:
        raise typer.Exit(code=fail(str(exc))) from exc
    typer.echo(f"Initialized {result.lakehouse_root}")
    typer.echo(f"Next: {result.next_command}")
