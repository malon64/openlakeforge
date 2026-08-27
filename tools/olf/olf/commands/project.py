"""Commands for validating a writable OpenLakeForge data project."""

from __future__ import annotations

from pathlib import Path

import typer

from olf.project import ProjectSpec, validate_project

app = typer.Typer(help="Validate and build writable OpenLakeForge data projects.")


@app.command("validate")
def validate(
    project: str = typer.Option(..., "--project", help="Explicit writable project root."),
) -> None:
    """Emit a machine-readable report for one project root."""
    from olf.distribution import runtime_layout

    layout = runtime_layout()
    report = validate_project(ProjectSpec(root=Path(project), distribution_root=layout.distribution_root))
    typer.echo(report.render_json())
    if not report.ok:
        raise typer.Exit(code=1)
