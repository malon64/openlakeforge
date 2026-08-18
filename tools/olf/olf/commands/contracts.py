"""Provider-contract runtime environment helpers."""

from __future__ import annotations

import os

import typer

from olf import config
from olf import contracts as contracts_module

app = typer.Typer(help="Provider-contract runtime environment helpers.")


@app.command("env")
def contracts_env(
    terraform_dir: str = typer.Option(
        "infra/terraform/environments/local",
        "--terraform-dir",
        help="Terraform root exposing the provider_contracts output.",
    ),
) -> None:
    """Print `export`/`unset` lines for the runtime contract environment.

    Falls back to local defaults when the Terraform stack has not been applied
    yet. Intended for `eval` from scripts/contracts/load-runtime-env.sh.
    """
    provider_contracts = contracts_module.load_provider_contracts(terraform_dir)
    exports, unsets = contracts_module.build_contract_env(os.environ, provider_contracts, repo_root=config.repo_root())
    output = contracts_module.render_shell_exports(exports, unsets)
    if output:
        typer.echo(output)


@app.command("check")
def contracts_check(
    repo_root: str = typer.Option(".", "--repo-root", help="Repository root to validate."),
) -> None:
    """Behavioral contract validation: HCL structure, descriptors, rendered outputs."""
    from olf import contracts_check as contracts_check_module

    report = contracts_check_module.run_contracts_check(repo_root)
    typer.echo(report.render())
    if not report.ok:
        raise typer.Exit(code=1)
