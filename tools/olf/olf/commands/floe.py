"""Floe profile and manifest helpers."""

from __future__ import annotations

import os

import typer

from olf import floe as floe_module
from olf.commands._shared import fail

app = typer.Typer(help="Floe profile and manifest helpers.")


@app.command("render-profile")
def floe_render_profile() -> None:
    """Render the Floe EnvironmentProfile YAML for the active contract env."""
    typer.echo(floe_module.render_profile(os.environ), nl=False)


@app.command("generate-manifests")
def generate_manifests(
    provider: str = typer.Option("local", "--provider", help="local, aws, or azure."),
    profile: str = typer.Option("full", "--profile", help="full or slim."),
    namespace: str = typer.Option("", "--namespace", help="Kubernetes namespace override."),
    cluster_name: str = typer.Option("", "--cluster-name", help="Local kind cluster name override."),
    kubeconfig_path: str = typer.Option("", "--kubeconfig-path", help="Kubeconfig file path override."),
) -> None:
    """Generate Floe manifests using resolved provider contracts, never shell exports."""
    from olf.commands.deployment import _build_context, _build_engine
    from olf.commands.runtime import _contract_terraform_dir
    from olf.deployment import contract_env
    from olf.deployment.errors import DeploymentError
    from olf.deployment.floe_manifests import generate_local_manifests
    from olf.deployment.local.artifacts import applied_contract_environment
    from olf.deployment.local.provider import LocalProvider

    try:
        context = _build_context(
            provider,
            profile=profile,
            namespace=namespace,
            cluster_name=cluster_name,
            kubeconfig_path=kubeconfig_path,
        )
        engine = _build_engine(context, var_file="")
        deployment_provider = engine.provider
        if isinstance(deployment_provider, LocalProvider):
            with applied_contract_environment(deployment_provider.config) as contract_environ:
                generate_local_manifests(
                    deployment_provider.config.floe,
                    deployment_provider.tools,
                    repo_root=context.paths.repo_root,
                    namespace=context.namespace,
                    governance_enabled=context.features.governance_enabled,
                    environ=contract_environ,
                    env=deployment_provider.env,
                )
        else:
            facts = deployment_provider._foundation_facts  # noqa: SLF001 - provider resolves cloud context once.
            with contract_env.applied_contract_environment(
                contract_terraform_dir=_contract_terraform_dir(context.paths.platform_terraform_dir),
                repo_root=context.paths.repo_root,
                namespace=context.namespace,
                kube_context=facts.kube_context,
                kubeconfig_path=context.paths.kubeconfig_path,
                port_forward_log_prefix=context.paths.port_forward_log_prefix,
            ) as contract_environ:
                deployment_provider.backend.generate_floe_manifests(
                    deployment_provider.config,
                    deployment_provider.tools,
                    repo_root=context.paths.repo_root,
                    namespace=context.namespace,
                    governance_enabled=context.features.governance_enabled,
                    environ=contract_environ,
                    env=deployment_provider.env,
                )
    except DeploymentError as exc:
        raise typer.Exit(code=fail(str(exc))) from exc
