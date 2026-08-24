"""dbt artifact preparation without a shell environment wrapper."""

from __future__ import annotations

import sys
from pathlib import Path

import typer

from olf import config
from olf.commands._shared import fail
from olf.deployment.engine import Toolkit
from olf.deployment.errors import DeploymentError

app = typer.Typer(help="Render and validate dbt product projects.")


@app.command("parse")
def parse(
    project_dir: str = typer.Option("", "--project-dir", help="One dbt project; defaults to all products."),
) -> None:
    """Render profiles, resolve dependencies, and parse discovered dbt projects."""
    root = config.repo_root()
    if str(root) not in sys.path:
        sys.path.insert(0, str(root))
    from libs.dbt.render_profiles import discover_project_dirs, write_profile

    projects = [Path(project_dir).resolve()] if project_dir else discover_project_dirs(root / "lakehouse_code/gold")
    if not projects:
        raise typer.Exit(code=fail("no product dbt projects found"))
    dbt = str(Toolkit.default().resolver.resolve("dbt"))
    try:
        for project in projects:
            write_profile(project, environment="local")
            for command in ("deps", "parse"):
                args = [dbt, command, "--project-dir", str(project)]
                if command == "parse":
                    args += ["--profiles-dir", str(project), "--target", "local"]
                Toolkit.default().runner.run(args, cwd=root, stream_output=True)
    except DeploymentError as exc:
        raise typer.Exit(code=fail(str(exc))) from exc
