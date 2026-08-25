"""`olf toolchain`: inspect and manage the versioned managed toolchain (#127).

Terraform/Helm/kubectl/kind are provisioned from `release/component-catalog.yaml`
into `OLF_HOME` (default `~/.openlakeforge`), scoped per distribution version
and platform. See `olf.toolchain` for the manager, and
`olf.tooling.resolver.build_resolver` for how deployment commands pick this
up automatically.
"""

from __future__ import annotations

import typer

from olf import config
from olf.commands._shared import fail

app = typer.Typer(help="Inspect and manage the managed CLI toolchain (Terraform, Helm, kubectl, kind).")


def _manager():  # noqa: ANN202
    from olf.toolchain.manager import ToolchainManager

    catalog_path = config.repo_root() / "release" / "component-catalog.yaml"
    return ToolchainManager.from_catalog_path(catalog_path)


@app.command("list")
def list_tools() -> None:
    """Show each managed tool's declared (catalog) and installed versions."""
    from olf.toolchain.errors import ToolchainError

    try:
        manager = _manager()
    except ToolchainError as exc:
        raise typer.Exit(code=fail(str(exc))) from exc

    typer.echo(f"distribution {manager.distribution_version}  platform {manager.platform}  home {manager.home}")
    for name, spec in manager.specs.items():
        installed = manager.installed(name)
        if installed is None:
            state = "not installed"
        elif installed.version == spec.version and installed.sha256 == spec.sha256:
            state = f"installed at {installed.path}"
        else:
            state = f"stale (installed {installed.version}, catalog wants {spec.version}) at {installed.path}"
        typer.echo(f"  {name:10} catalog={spec.version:12} {state}")


@app.command("install")
def install_tools(
    tool: str = typer.Option("", "--tool", help="Provision only this tool; default provisions all managed tools."),
) -> None:
    """Provision managed tools from the release catalog (CI calls this to pre-warm the cache)."""
    from olf.deployment.errors import DeploymentError
    from olf.toolchain.errors import ToolchainError

    try:
        manager = _manager()
        if tool:
            if tool not in manager.specs:
                raise typer.Exit(code=fail(f"unknown --tool: {tool!r} (expected one of {tuple(manager.specs)})"))
            path = manager.resolve(tool)
            typer.echo(f"{tool} -> {path}")
            return
        for name, path in manager.ensure_all().items():
            typer.echo(f"{name} -> {path}")
    except (ToolchainError, DeploymentError) as exc:
        raise typer.Exit(code=fail(str(exc))) from exc


@app.command("path")
def path(
    tool: str = typer.Argument("", help="Print only this tool's resolved path; default prints the managed bin dir."),
) -> None:
    """Print the managed bin directory, or one tool's resolved (and provisioned) path."""
    from olf.deployment.errors import DeploymentError
    from olf.toolchain.errors import ToolchainError

    try:
        manager = _manager()
        if not tool:
            typer.echo(str(manager.bin_dir))
            return
        if tool not in manager.specs:
            raise typer.Exit(code=fail(f"unknown tool: {tool!r} (expected one of {tuple(manager.specs)})"))
        typer.echo(str(manager.resolve(tool)))
    except (ToolchainError, DeploymentError) as exc:
        raise typer.Exit(code=fail(str(exc))) from exc


@app.command("clean")
def clean(
    version: str = typer.Option("", "--version", help="Remove only this distribution version's toolchain."),
    keep_current: bool = typer.Option(
        False, "--keep-current", help="Remove every installed version except the current distribution's."
    ),
    all_versions: bool = typer.Option(False, "--all", help="Remove every installed toolchain version."),
) -> None:
    """Remove installed toolchain versions under OLF_HOME. Never touches host-installed tools."""
    from olf.toolchain.errors import ToolchainError

    selected = sum(bool(x) for x in (version, keep_current, all_versions))
    if selected != 1:
        raise typer.Exit(code=fail("exactly one of --version, --keep-current, or --all is required"))
    try:
        manager = _manager()
        removed = manager.prune(
            version=version or None,
            keep_current=keep_current,
            remove_all=all_versions,
        )
    except ToolchainError as exc:
        raise typer.Exit(code=fail(str(exc))) from exc
    if not removed:
        typer.echo("Nothing to remove.")
        return
    for entry in removed:
        typer.echo(f"removed {entry}")
