"""Floe profile and manifest helpers."""

from __future__ import annotations

import os

import typer

from olf import floe as floe_module

app = typer.Typer(help="Floe profile and manifest helpers.")


@app.command("render-profile")
def floe_render_profile() -> None:
    """Render the Floe EnvironmentProfile YAML for the active contract env."""
    typer.echo(floe_module.render_profile(os.environ), nl=False)
