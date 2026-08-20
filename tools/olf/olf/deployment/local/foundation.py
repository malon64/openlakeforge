"""Local kind cluster foundation lifecycle.

Port of `scripts/local/foundation/{up,down}.sh`. Terraform remains the owner
of the kind cluster (`terraform_data.kind_cluster` in
`infra/terraform/foundations/local-kind`) - this module only orchestrates
Terraform/kind/kubectl calls, it never creates or deletes the cluster
directly.
"""

from __future__ import annotations

from collections.abc import Mapping

from olf import log
from olf.deployment.engine import Toolkit
from olf.deployment.errors import DeploymentPreconditionError
from olf.deployment.local.config import LocalDeploymentConfig
from olf.tooling.kubectl import KubeContextUnreachableError


def foundation_apply_variables(config: LocalDeploymentConfig) -> dict[str, str]:
    return {
        "cluster_name": config.cluster.name,
        "cluster_config_path": str(config.cluster.config_path),
        "kubeconfig_path": str(config.paths.kubeconfig_path),
        "kind_wait_timeout": config.cluster.wait_timeout,
        "reset_existing_cluster": "true" if config.cluster.reset_existing else "false",
    }


def foundation_destroy_variables(config: LocalDeploymentConfig) -> dict[str, str]:
    return {
        "cluster_name": config.cluster.name,
        "cluster_config_path": str(config.cluster.config_path),
        "kubeconfig_path": str(config.paths.kubeconfig_path),
    }


def foundation_up(config: LocalDeploymentConfig, tools: Toolkit, *, env: Mapping[str, str]) -> None:
    foundation_dir = config.paths.foundation_terraform_dir
    config.context.prepare_directories()

    log.step("Initializing Terraform local kind foundation...")
    tools.terraform.init(foundation_dir, env=env)

    log.step("Applying Terraform local kind foundation...")
    tools.terraform.apply(foundation_dir, variables=foundation_apply_variables(config), env=env)

    tools.kind.export_kubeconfig(config.cluster.name, kubeconfig_path=config.paths.kubeconfig_path, env=env)

    try:
        tools.kubectl.require_context_reachable(
            config.kube_context, kubeconfig=config.paths.kubeconfig_path, env=env
        )
    except KubeContextUnreachableError as exc:
        raise DeploymentPreconditionError(
            f"Kubernetes context '{config.kube_context}' is not reachable after applying the foundation: {exc}"
        ) from exc

    log.step(f"Local foundation is ready. Kubernetes context: {config.kube_context}")


def foundation_down(
    config: LocalDeploymentConfig,
    tools: Toolkit,
    *,
    env: Mapping[str, str],
    force: bool = False,
) -> None:
    foundation_dir = config.paths.foundation_terraform_dir

    log.step("Initializing Terraform local kind foundation...")
    tools.terraform.init(foundation_dir, env=env)

    state_result = tools.terraform.state_show(foundation_dir, "terraform_data.kind_cluster", env=env, check=False)
    cluster_exists = config.cluster.name in tools.kind.get_clusters(env=env)

    if not state_result.ok:
        if cluster_exists:
            raise DeploymentPreconditionError(
                f"kind cluster '{config.cluster.name}' exists, but the local foundation Terraform state "
                "does not own it. Run 'olf deploy --provider local --phase foundation' first to adopt the "
                "existing cluster into the foundation state."
            )
        log.step(f"No local foundation state or kind cluster exists for '{config.cluster.name}'.")
        return

    if not force and cluster_exists:
        namespace_result = tools.kubectl.get(
            "namespace",
            name=config.namespace,
            context=config.kube_context,
            kubeconfig=config.paths.kubeconfig_path,
            env=env,
            check=False,
        )
        if namespace_result.ok:
            raise DeploymentPreconditionError(
                f"namespace '{config.namespace}' still exists on '{config.kube_context}'. Run "
                "'olf destroy --provider local --phase platform' before destroying the local foundation. "
                "Pass --force only if you intentionally want to delete the cluster with platform resources "
                "still present."
            )

    log.step("Destroying Terraform local kind foundation...")
    tools.terraform.destroy(foundation_dir, variables=foundation_destroy_variables(config), env=env)
    log.step("Local foundation is destroyed.")
