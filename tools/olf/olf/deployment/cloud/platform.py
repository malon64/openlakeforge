"""Cloud (AWS/Azure) static platform Terraform orchestration.

Port of `scripts/{aws,azure}/stack/platform-up.sh`. Reuses `deployment.
charts.prepare_cached_chart`/`prepare_cached_dagster_chart_no_schema` and
`deployment.kube_ops` exactly as the local provider does; only the
Superset-image build and the Terraform variables differ by provider, both
routed through `CloudBackend`.
"""

from __future__ import annotations

from collections.abc import Mapping

from olf import log
from olf.deployment import kube_ops
from olf.deployment.charts import ChartRequest, prepare_cached_chart, prepare_cached_dagster_chart_no_schema
from olf.deployment.cloud.backend import CloudBackend, FoundationFacts
from olf.deployment.cloud.config import CloudDeploymentConfig
from olf.deployment.engine import Toolkit
from olf.deployment.errors import CommandExecutionError
from olf.deployment.retry import RetryPolicy, run_with_retry

_NAMESPACE_RESOURCE_ADDR = "kubernetes_namespace_v1.lakehouse"
_POLARIS_JOB_PREFIXES = ("polaris-bootstrap-", "polaris-metastore-bootstrap-")


def _retry_if_logging(description: str, policy: RetryPolicy):  # noqa: ANN202
    def _predicate(exc: CommandExecutionError, attempt: int) -> bool:  # noqa: ARG001
        if attempt < policy.max_attempts:
            delay = policy.delay_for_attempt(attempt)
            log.warn(f"{description} failed on attempt {attempt}/{policy.max_attempts}; retrying in {delay:g}s...")
        else:
            log.error(f"{description} failed after {attempt} attempt(s).")
        return True

    return _predicate


def _prepare_charts(config: CloudDeploymentConfig, tools: Toolkit, *, env: Mapping[str, str]) -> None:
    prepare_cached_chart(
        ChartRequest(
            display_name="Trino",
            repo_name="trino",
            repo_url=config.charts.trino_repository_url,
            chart_ref=config.charts.trino_chart_ref,
            version=config.charts.trino_version,
            package_path=config.charts.trino_package_path,
        ),
        helm=tools.helm,
        paths=config.paths,
        env=env,
        retry_policy=config.terraform.apply_retry,
    )
    prepare_cached_dagster_chart_no_schema(
        ChartRequest(
            display_name="Dagster",
            repo_name="dagster",
            repo_url=config.charts.dagster_repository_url,
            chart_ref=config.charts.dagster_chart_ref,
            version=config.charts.dagster_version,
            package_path=config.charts.dagster_package_path,
        ),
        helm=tools.helm,
        paths=config.paths,
        env=env,
        retry_policy=config.terraform.apply_retry,
    )


def platform_up(
    config: CloudDeploymentConfig,
    tools: Toolkit,
    backend: CloudBackend,
    facts: FoundationFacts,
    *,
    env: Mapping[str, str],
) -> None:
    platform_dir = config.paths.platform_terraform_dir

    if config.features.analytics_enabled:
        from olf.deployment.cloud import images

        images.build_and_push_superset_image(config, tools, backend, facts, env=env)
    else:
        log.step("Skipping Superset image build: analytics layer is disabled.")

    config.context.prepare_directories()
    _prepare_charts(config, tools, env=env)

    log.step(f"Initializing Terraform {backend.scope} platform...")
    tools.terraform.init(platform_dir, env=env)

    variables = backend.platform_apply_variables(config, facts)
    var_files = (str(config.terraform.var_file),) if config.terraform.var_file is not None else ()

    kube_ops.import_namespace_if_missing_in_state(
        tools.terraform,
        tools.kubectl,
        terraform_dir=platform_dir,
        resource_addr=_NAMESPACE_RESOURCE_ADDR,
        namespace=config.namespace,
        var_files=var_files,
        variables=variables,
        context=facts.kube_context,
        kubeconfig=config.paths.kubeconfig_path,
        env=env,
    )

    def _apply_once() -> None:
        if backend.cleanup_polaris_jobs_before_apply():
            for prefix in _POLARIS_JOB_PREFIXES:
                kube_ops.cleanup_failed_jobs_by_prefix(
                    tools.kubectl,
                    prefix,
                    namespace=config.namespace,
                    context=facts.kube_context,
                    kubeconfig=config.paths.kubeconfig_path,
                    env=env,
                )
        tools.terraform.apply(platform_dir, var_files=var_files, variables=variables, env=env)

    log.step(f"Applying Terraform {backend.scope} platform...")
    run_with_retry(
        _apply_once,
        policy=config.terraform.apply_retry,
        retry_if=_retry_if_logging("Terraform apply", config.terraform.apply_retry),
    )

    log.step(f"Static OpenLakeForge {backend.scope} platform is applied.")
