"""End-to-end environment validation."""

from __future__ import annotations

import os
from pathlib import Path

import typer

from olf import config
from olf.commands._shared import deployment_context, fail

app = typer.Typer(help="End-to-end environment validation.")


def _resolve_kubeconfig_path(env: str, default: Path) -> Path:
    """Mirror `olf.e2e._runner.configure_kubeconfig`'s override precedence.

    This command exports `KUBECONFIG` via `applied_contract_environment`
    before `e2e.run()` (and thus `_runner.configure_kubeconfig`) ever runs -
    if it only consulted the bare `KUBECONFIG` env var here, it would shadow
    the documented per-provider override (`AWS_KUBECONFIG_PATH`/
    `AZURE_KUBECONFIG_PATH`) with the plain default path before `_runner`
    gets a chance to fall back to it. `default` is the resolved
    `DeploymentContext`'s own kubeconfig path - the same one `olf deploy`
    wrote to, so an installed distribution's `olf e2e run` finds it without
    the caller having to set `KUBECONFIG` by hand.
    """
    provider_override = config.env(f"{env.upper()}_KUBECONFIG_PATH")
    configured = config.env("KUBECONFIG") or provider_override
    if configured:
        return Path(configured)
    return default


def _default_kube_context(env: str) -> str:
    """Mirror `olf.e2e._runner`'s own per-environment `KUBE_CONTEXT` fallback.

    Must match exactly: this value is what `e2e.run()` would derive on its
    own from `os.environ.get("KUBE_CONTEXT", ...)` if that variable were
    left unset - but `applied_contract_environment` always sets
    `KUBE_CONTEXT` in `os.environ` (even to a resolved default), so it has
    to resolve the same fallback here to avoid exporting an empty string
    for `aws-e2e`/`azure-e2e`, which don't set `KUBE_CONTEXT` themselves.
    """
    if env == "local":
        cluster_name = config.env("CLUSTER_NAME", "openlakeforge-local")
        return f"kind-{cluster_name}"
    if env == "azure":
        return config.env("AZURE_CLUSTER_NAME", "aks-openlakeforge-poc")
    # Must match `cloud/aws.py::_DEFAULT_CLUSTER_NAME` - a direct
    # `olf deploy --provider aws` with no AWS_CLUSTER_NAME set creates this
    # cluster; e2e (also unset) must target the same one.
    return config.env("AWS_CLUSTER_NAME", "limited-eks-openlakeforge-poc")


@app.command("run")
def e2e_run(
    env: str = typer.Option(..., "--env", help="Environment to validate: local, azure, or aws."),
    suite: str = typer.Option("", "--suite", help="Suite to run: full or smoke. Defaults to full."),
    project_root: str = typer.Option(
        "", "--project-root", help="Writable project root; defaults to the current directory."
    ),
) -> None:
    """Run end-to-end validation for a deployed OpenLakeForge environment.

    Self-sufficient: resolves and applies the provider-contract environment
    itself (the in-process equivalent of sourcing `scripts/contracts/
    load-runtime-env.sh` before this ran), so `olf e2e run --env aws` needs
    no shell wrapper around it.

    Resolves the same installed-vs-source runtime layout `olf deploy` does
    (`deployment_context`) - an installed distribution's Terraform roots,
    kubeconfig, and descriptors live under `OLF_HOME`/the extracted payload,
    not the caller's current directory, so validating the deployment just
    created requires the identical path resolution `olf deploy` used.
    """
    from olf import e2e
    from olf.deployment import contract_env
    from olf.deployment.errors import DeploymentError

    valid_envs = {"local", "azure", "aws"}
    valid_suites = {"", "full", "smoke"}
    if env not in valid_envs:
        raise typer.Exit(code=fail(f"unknown --env {env!r}; expected one of: {', '.join(sorted(valid_envs))}."))
    if suite not in valid_suites:
        raise typer.Exit(code=fail(f"unknown --suite {suite!r}; expected 'full' or 'smoke'."))

    context = deployment_context(env, profile="full", namespace="", cluster_name="", project_root=project_root)
    repo_root = context.paths.repo_root
    distribution_root = context.paths.distribution_root
    contract_dir = Path(
        config.env("OPENLAKEFORGE_CONTRACT_TERRAFORM_DIR", str(context.paths.platform_terraform_dir))
    )
    kubeconfig_path = _resolve_kubeconfig_path(env, context.paths.kubeconfig_path)
    port_forward_log_prefix = Path(
        config.env("OPENLAKEFORGE_PORT_FORWARD_LOG_PREFIX", str(context.paths.port_forward_log_prefix))
    )
    kube_context = config.env("KUBE_CONTEXT") or _default_kube_context(env)
    # Fresh contract readers create their own managed-tool resolver and,
    # for an installed distribution, must resolve the same external
    # Terraform state/data roots `olf deploy` wrote to (see
    # `DeploymentContext.command_env` and `olf.tooling.terraform.
    # external_state_options`) - without this, the contract read below
    # silently targets the payload's absent default state.
    environ = context.command_env(base=os.environ)

    try:
        with contract_env.applied_contract_environment(
            contract_terraform_dir=contract_dir,
            repo_root=repo_root,
            namespace=config.namespace(),
            kube_context=kube_context,
            kubeconfig_path=kubeconfig_path,
            port_forward_log_prefix=port_forward_log_prefix,
            environ=environ,
        ):
            e2e.run(
                env,  # type: ignore[arg-type]
                suite=suite or None,  # type: ignore[arg-type]
                namespace=config.namespace(),
                kube_context=kube_context,
                repo_root=repo_root,
                distribution_root=distribution_root,
            )
    except (e2e.E2EError, DeploymentError) as exc:
        # DeploymentError covers a managed-toolchain provisioning failure
        # (#127) raised while resolving the provider-contract environment,
        # before e2e.run() (and its own E2EError-only preflight) is ever
        # reached - it must fail the same clean way, not escape as a raw
        # traceback.
        raise typer.Exit(code=fail(str(exc))) from exc
