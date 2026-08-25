"""Local static platform Terraform orchestration.

Port of `scripts/local/stack/platform-up.sh`. Preserves the exact drift
recovery, namespace adoption, Polaris job cleanup, and bounded-retry
semantics of the shell script.
"""

from __future__ import annotations

from collections.abc import Mapping

from olf import log
from olf.deployment import kube_ops
from olf.deployment.charts import ChartRequest, prepare_cached_chart
from olf.deployment.engine import Toolkit
from olf.deployment.errors import CommandExecutionError, DeploymentPreconditionError
from olf.deployment.local.config import LocalDeploymentConfig
from olf.deployment.retry import RetryPolicy, run_with_retry
from olf.tooling.kubectl import KubeContextUnreachableError

_SEAWEEDFS_RESOURCE_ADDR = "module.seaweedfs.helm_release.seaweedfs"
_NAMESPACE_RESOURCE_ADDR = "kubernetes_namespace_v1.lakehouse"
_POLARIS_JOB_PREFIXES = ("polaris-bootstrap-", "polaris-metastore-bootstrap-")


def platform_var_files(config: LocalDeploymentConfig) -> tuple[str, ...]:
    return (str(config.terraform.var_file),) if config.terraform.var_file is not None else ()


def prepare_charts(config: LocalDeploymentConfig, tools: Toolkit, *, env: Mapping[str, str]) -> None:
    """Ensure Terraform's locally referenced Trino chart archive is available."""
    config.context.prepare_directories()
    prepare_cached_chart(
        ChartRequest(
            display_name="Trino",
            repo_name="trino",
            repo_url=config.charts.trino_repository_url,
            chart_ref=config.charts.trino_chart_ref,
            version=config.charts.trino_version,
            package_path=config.charts.trino_package_path,
            sha256=config.charts.trino_sha256,
        ),
        helm=tools.helm,
        paths=config.paths,
        env=env,
        retry_policy=config.terraform.apply_retry,
    )


def platform_apply_variables(config: LocalDeploymentConfig) -> dict[str, str]:
    images = config.images
    return {
        "namespace": config.namespace,
        "kube_context": config.kube_context,
        "kubeconfig_path": str(config.paths.kubeconfig_path),
        "foundation_state_path": str(config.paths.foundation_state_path),
        "project_code_image_repository": images.project_code_repository,
        "project_code_image_tag": images.project_code_tag,
        "project_code_image_pull_policy": images.project_code_pull_policy,
        "project_code_image_revision": images.project_code_revision,
        "enable_governance": "true" if config.features.governance_enabled else "false",
        "enable_analytics": "true" if config.features.analytics_enabled else "false",
        "superset_image_repository": images.superset_repository,
        "superset_image_tag": images.superset_tag,
        "superset_image_pull_policy": images.superset_pull_policy,
        "trino_chart_package_path": str(config.charts.trino_package_path),
    }


def platform_destroy_variables(config: LocalDeploymentConfig) -> dict[str, str]:
    return {
        "namespace": config.namespace,
        "kube_context": config.kube_context,
        "kubeconfig_path": str(config.paths.kubeconfig_path),
        "foundation_state_path": str(config.paths.foundation_state_path),
    }


def reset_drifted_platform_if_needed(config: LocalDeploymentConfig, tools: Toolkit, *, env: Mapping[str, str]) -> bool:
    platform_dir = config.paths.platform_terraform_dir
    if not kube_ops.namespace_exists(
        tools.kubectl, config.namespace, context=config.kube_context, kubeconfig=config.paths.kubeconfig_path, env=env
    ):
        return False
    if kube_ops.state_has_resource(tools.terraform, platform_dir, _SEAWEEDFS_RESOURCE_ADDR, env=env):
        return False

    log.warn("local platform resources exist in-cluster but Terraform state is missing core objects.")
    log.warn("resetting the local platform before apply to recover from state drift...")

    from olf.deployment.local import teardown

    teardown.platform_down(config, tools, env=env)

    log.step("Reinitializing Terraform after local platform reset...")
    tools.terraform.init(platform_dir, env=env)
    return True


def _retry_if_logging(description: str, policy: RetryPolicy):  # noqa: ANN202
    def _predicate(exc: CommandExecutionError, attempt: int) -> bool:  # noqa: ARG001
        if attempt < policy.max_attempts:
            delay = policy.delay_for_attempt(attempt)
            log.warn(f"{description} failed on attempt {attempt}/{policy.max_attempts}; retrying in {delay:g}s...")
        else:
            log.error(f"{description} failed after {attempt} attempt(s).")
        return True

    return _predicate


def platform_up(config: LocalDeploymentConfig, tools: Toolkit, *, env: Mapping[str, str]) -> None:
    platform_dir = config.paths.platform_terraform_dir

    if not config.paths.foundation_state_path.is_file():
        raise DeploymentPreconditionError(
            f"local foundation Terraform state is missing: {config.paths.foundation_state_path}. "
            "Run the foundation phase before applying the local platform."
        )
    try:
        tools.kubectl.require_context_reachable(config.kube_context, kubeconfig=config.paths.kubeconfig_path, env=env)
    except KubeContextUnreachableError as exc:
        raise DeploymentPreconditionError(
            f"Kubernetes context '{config.kube_context}' is not reachable. "
            "Run the foundation phase before applying the local platform."
        ) from exc

    if config.features.analytics_enabled:
        from olf.deployment.local import images

        images.prepare_superset_image(config, tools, env=env)
    else:
        log.step("Skipping Superset image build: analytics layer is disabled.")

    prepare_charts(config, tools, env=env)

    log.step("Initializing Terraform...")
    tools.terraform.init(platform_dir, env=env)

    reset_drifted_platform_if_needed(config, tools, env=env)

    variables = platform_apply_variables(config)
    var_files = platform_var_files(config)

    kube_ops.import_namespace_if_missing_in_state(
        tools.terraform,
        tools.kubectl,
        terraform_dir=platform_dir,
        resource_addr=_NAMESPACE_RESOURCE_ADDR,
        namespace=config.namespace,
        var_files=var_files,
        variables=variables,
        context=config.kube_context,
        kubeconfig=config.paths.kubeconfig_path,
        env=env,
    )

    def _apply_once() -> None:
        for prefix in _POLARIS_JOB_PREFIXES:
            kube_ops.cleanup_failed_jobs_by_prefix(
                tools.kubectl,
                prefix,
                namespace=config.namespace,
                context=config.kube_context,
                kubeconfig=config.paths.kubeconfig_path,
                env=env,
            )
        tools.terraform.apply(platform_dir, var_files=var_files, variables=variables, env=env)

    log.step("Applying Terraform local platform...")
    run_with_retry(
        _apply_once,
        policy=config.terraform.apply_retry,
        retry_if=_retry_if_logging("Terraform apply", config.terraform.apply_retry),
    )

    log.step("Static OpenLakeForge local platform is applied.")
