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
    provider: str = typer.Option("local", "--provider", help="Provider owning the deployed contracts."),
    profile: str = typer.Option("full", "--profile", help="full or slim."),
    namespace: str = typer.Option("", "--namespace", help="Kubernetes namespace override."),
    cluster_name: str = typer.Option("", "--cluster-name", help="Local kind cluster name override."),
    kubeconfig_path: str = typer.Option("", "--kubeconfig-path", help="Kubeconfig file path override."),
) -> None:
    """Render profiles, resolve dependencies, and parse projects using provider contracts."""
    from olf.commands.runtime import provider_contract_environment

    root = config.repo_root()
    if str(root) not in sys.path:
        sys.path.insert(0, str(root))
    from libs.dbt.render_profiles import discover_project_dirs, write_profile

    projects = [Path(project_dir).resolve()] if project_dir else discover_project_dirs(root / "lakehouse_code/gold")
    if not projects:
        raise typer.Exit(code=fail("no product dbt projects found"))
    try:
        with provider_contract_environment(
            provider=provider,
            profile=profile,
            namespace=namespace,
            cluster_name=cluster_name,
            kubeconfig_path=kubeconfig_path,
        ):
            tools = Toolkit.default()
            dbt = str(tools.resolver.resolve("dbt"))
            for project in projects:
                write_profile(project, environment=provider)
                for command in ("deps", "parse"):
                    args = [dbt, command, "--project-dir", str(project)]
                    if command == "parse":
                        args += ["--profiles-dir", str(project), "--target", provider]
                    tools.runner.run(args, cwd=root, stream_output=True)
    except DeploymentError as exc:
        raise typer.Exit(code=fail(str(exc))) from exc
