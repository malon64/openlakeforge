"""End-to-end environment validation."""

from __future__ import annotations

import typer

from olf import config
from olf.commands._shared import fail

app = typer.Typer(help="End-to-end environment validation.")


@app.command("run")
def e2e_run(
    env: str = typer.Option(..., "--env", help="Environment to validate: local, azure, or aws."),
    suite: str = typer.Option("", "--suite", help="Suite to run: full or smoke. Defaults to full."),
) -> None:
    """Run end-to-end validation for a deployed OpenLakeForge environment."""
    from olf import e2e

    valid_envs = {"local", "azure", "aws"}
    valid_suites = {"", "full", "smoke"}
    if env not in valid_envs:
        raise typer.Exit(code=fail(f"unknown --env {env!r}; expected one of: {', '.join(sorted(valid_envs))}."))
    if suite not in valid_suites:
        raise typer.Exit(code=fail(f"unknown --suite {suite!r}; expected 'full' or 'smoke'."))
    try:
        e2e.run(
            env,  # type: ignore[arg-type]
            suite=suite or None,  # type: ignore[arg-type]
            namespace=config.namespace(),
            kube_context=config.env("KUBE_CONTEXT"),
        )
    except e2e.E2EError as exc:
        raise typer.Exit(code=fail(str(exc))) from exc
