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
from olf.deployment.errors import CommandExecutionError, DeploymentPreconditionError, ExecutableNotFoundError
from olf.deployment.local.config import LocalDeploymentConfig
from olf.tooling.kubectl import KubeContextUnreachableError


def foundation_apply_variables(config: LocalDeploymentConfig, tools: Toolkit) -> dict[str, str]:
    """`kind_executable_path`/`kubectl_executable_path` route the foundation
    Terraform root's `local-exec` provisioner (`infra/terraform/foundations/
    local-kind/main.tf`) through the same resolver every other `olf`-invoked
    tool uses, instead of it invoking bare `kind`/`kubectl` from whatever
    happens to be on PATH when Terraform runs - which, under the managed
    toolchain (#127), is not the same binary this `Toolkit` resolved.
    """
    return {
        "cluster_name": config.cluster.name,
        "cluster_config_path": str(config.cluster.config_path),
        "kubeconfig_path": str(config.paths.kubeconfig_path),
        "kind_wait_timeout": config.cluster.wait_timeout,
        "reset_existing_cluster": "true" if config.cluster.reset_existing else "false",
        "kind_executable_path": str(tools.resolver.resolve("kind")),
        "kubectl_executable_path": str(tools.resolver.resolve("kubectl")),
    }


def foundation_destroy_variables(config: LocalDeploymentConfig, tools: Toolkit) -> dict[str, str]:
    return {
        "cluster_name": config.cluster.name,
        "cluster_config_path": str(config.cluster.config_path),
        "kubeconfig_path": str(config.paths.kubeconfig_path),
        "kind_executable_path": str(tools.resolver.resolve("kind")),
    }


def foundation_up(config: LocalDeploymentConfig, tools: Toolkit, *, env: Mapping[str, str]) -> None:
    foundation_dir = config.paths.foundation_terraform_dir
    config.context.prepare_directories()

    _require_docker_reachable(tools, env=env)

    log.step("Initializing Terraform local kind foundation...")
    tools.terraform.init(foundation_dir, env=env)

    log.step("Applying Terraform local kind foundation...")
    tools.terraform.apply(foundation_dir, variables=foundation_apply_variables(config, tools), env=env)

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


def _require_docker_reachable(tools: Toolkit, *, env: Mapping[str, str]) -> None:
    """Fail with an actionable message before Terraform's local-exec
    provisioner (`infra/terraform/foundations/local-kind/main.tf`) hits its
    own generic "Docker daemon is not running or not accessible" check - the
    actual cause in #156 was a stale Docker Desktop socket surviving under a
    scoped, context-less DOCKER_CONFIG on a Colima-only host.
    """
    try:
        tools.docker.version(env=env)
    except (CommandExecutionError, ExecutableNotFoundError) as exc:
        endpoint = env.get("DOCKER_HOST", "the default Docker context (no DOCKER_HOST was resolved)")
        raise DeploymentPreconditionError(
            f"Docker is not reachable at {endpoint}: {exc}\n"
            "Compare this against 'docker context show' on this host, or set DOCKER_HOST "
            "explicitly to override endpoint resolution."
        ) from exc


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
        # Every namespace the deployment owns, not just the selected stage's:
        # the shared namespace holds PostgreSQL and its PVC, so a partially
        # torn-down platform would otherwise let this delete the cluster and
        # the metadata with it.
        remaining = [
            namespace
            for namespace in config.context.owned_namespaces
            if tools.kubectl.get(
                "namespace",
                name=namespace,
                context=config.kube_context,
                kubeconfig=config.paths.kubeconfig_path,
                env=env,
                check=False,
            ).ok
        ]
        if remaining:
            raise DeploymentPreconditionError(
                f"namespace(s) {', '.join(remaining)} still exist on '{config.kube_context}'. Run "
                "'olf destroy --provider local --phase platform' before destroying the local foundation. "
                "Pass --force only if you intentionally want to delete the cluster with platform resources "
                "still present."
            )

    log.step("Destroying Terraform local kind foundation...")
    tools.terraform.destroy(foundation_dir, variables=foundation_destroy_variables(config, tools), env=env)
    log.step("Local foundation is destroyed.")
