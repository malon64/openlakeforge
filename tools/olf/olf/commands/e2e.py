"""End-to-end environment validation."""

from __future__ import annotations

from pathlib import Path

import typer

from olf import config
from olf.commands._shared import fail

app = typer.Typer(help="End-to-end environment validation.")

_DEFAULT_CONTRACT_TERRAFORM_DIRS = {
    "local": "infra/terraform/environments/local",
    "azure": "infra/terraform/environments/azure-poc",
    "aws": "infra/terraform/environments/aws-poc",
}


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
    return config.env("AWS_CLUSTER_NAME", "limited-eks-openlakeforge-poc")


@app.command("run")
def e2e_run(
    env: str = typer.Option(..., "--env", help="Environment to validate: local, azure, or aws."),
    suite: str = typer.Option("", "--suite", help="Suite to run: full or smoke. Defaults to full."),
) -> None:
    """Run end-to-end validation for a deployed OpenLakeForge environment.

    Self-sufficient: resolves and applies the provider-contract environment
    itself (the in-process equivalent of sourcing `scripts/contracts/
    load-runtime-env.sh` before this ran), so `olf e2e run --env aws` needs
    no shell wrapper around it.
    """
    from olf import e2e
    from olf.deployment import contract_env

    valid_envs = {"local", "azure", "aws"}
    valid_suites = {"", "full", "smoke"}
    if env not in valid_envs:
        raise typer.Exit(code=fail(f"unknown --env {env!r}; expected one of: {', '.join(sorted(valid_envs))}."))
    if suite not in valid_suites:
        raise typer.Exit(code=fail(f"unknown --suite {suite!r}; expected 'full' or 'smoke'."))

    repo_root = config.repo_root()
    contract_dir = Path(
        config.env("OPENLAKEFORGE_CONTRACT_TERRAFORM_DIR", str(repo_root / _DEFAULT_CONTRACT_TERRAFORM_DIRS[env]))
    )
    kubeconfig_path = Path(config.env("KUBECONFIG", str(repo_root / f".tmp/kubeconfigs/{env}.yaml")))
    port_forward_log_prefix = Path(
        config.env("OPENLAKEFORGE_PORT_FORWARD_LOG_PREFIX", f"/tmp/openlakeforge-{env}")
    )
    kube_context = config.env("KUBE_CONTEXT") or _default_kube_context(env)

    try:
        with contract_env.applied_contract_environment(
            contract_terraform_dir=contract_dir,
            repo_root=repo_root,
            namespace=config.namespace(),
            kube_context=kube_context,
            kubeconfig_path=kubeconfig_path,
            port_forward_log_prefix=port_forward_log_prefix,
        ):
            e2e.run(
                env,  # type: ignore[arg-type]
                suite=suite or None,  # type: ignore[arg-type]
                namespace=config.namespace(),
                kube_context=kube_context,
                repo_root=repo_root,
            )
    except e2e.E2EError as exc:
        raise typer.Exit(code=fail(str(exc))) from exc
