"""`CloudBackend` for AWS EKS/ECR.

Port of the AWS-specific branches of `scripts/aws/{foundation,stack,images}/
*.sh`: caller-identity preflight, EKS Terraform variables, `eks update-
kubeconfig`, ECR login, and the AWS Glue Floe profile strategy.
"""

from __future__ import annotations

from collections.abc import Mapping
from pathlib import Path

from olf import log
from olf.deployment.cloud.backend import FoundationFacts
from olf.deployment.cloud.config import CloudDeploymentConfig
from olf.deployment.engine import Toolkit
from olf.deployment.env_settings import env as _env
from olf.deployment.errors import CommandExecutionError, DeploymentPreconditionError, ExecutableNotFoundError
from olf.deployment.floe_manifests import generate_aws_manifests
from olf.deployment.portforward import ForwardTarget

_DEFAULT_CLUSTER_NAME = "eks-openlakeforge-poc"


class AwsBackend:
    scope = "aws"

    def preflight(self, tools: Toolkit, *, env: Mapping[str, str]) -> None:
        try:
            tools.aws.sts_get_caller_identity(env=env)
        except (CommandExecutionError, ExecutableNotFoundError) as exc:
            raise DeploymentPreconditionError(
                f"AWS CLI is not authenticated (aws sts get-caller-identity failed): {exc}"
            ) from exc

    def foundation_state_resource_addr(self) -> str:
        return "aws_eks_cluster.this"

    def _foundation_variables(self, config: CloudDeploymentConfig, environ: Mapping[str, str]) -> dict[str, str]:
        cluster_name = _env(environ, "AWS_CLUSTER_NAME", _DEFAULT_CLUSTER_NAME)
        instance_types_raw = _env(environ, "AWS_NODE_INSTANCE_TYPES", "m7i.large")
        instance_types = instance_types_raw if instance_types_raw.startswith("[") else f'["{instance_types_raw}"]'
        return {
            "cluster_name": cluster_name,
            "kubeconfig_path": str(config.paths.kubeconfig_path),
            "aws_region": _env(environ, "AWS_REGION", "eu-west-1"),
            "node_desired_size": _env(environ, "AWS_NODE_DESIRED_SIZE", "3"),
            "node_min_size": _env(environ, "AWS_NODE_MIN_SIZE", "1"),
            "node_max_size": _env(environ, "AWS_NODE_MAX_SIZE", "4"),
            "node_instance_types": instance_types,
        }

    def foundation_apply_variables(
        self, config: CloudDeploymentConfig, environ: Mapping[str, str]
    ) -> dict[str, str]:
        return self._foundation_variables(config, environ)

    def foundation_destroy_variables(
        self, config: CloudDeploymentConfig, environ: Mapping[str, str]
    ) -> dict[str, str]:
        return self._foundation_variables(config, environ)

    def foundation_tfvars_file(
        self, environ: Mapping[str, str], *, repo_root: Path, foundation_terraform_dir: Path
    ) -> Path | None:
        raw = environ.get("AWS_TFVARS_FILE") or ""
        if raw:
            candidate = Path(raw)
            return candidate if candidate.is_absolute() else repo_root / candidate
        default_tfvars = foundation_terraform_dir / "sandbox.tfvars"
        return default_tfvars if default_tfvars.is_file() else None

    def resolve_foundation_facts(
        self, tools: Toolkit, *, foundation_terraform_dir: Path, env: Mapping[str, str]
    ) -> FoundationFacts:
        cluster_name = tools.terraform.output_raw(foundation_terraform_dir, "cluster_name", env=env)
        aws_region = tools.terraform.output_raw(foundation_terraform_dir, "aws_region", env=env)
        project_code_repository = tools.terraform.output_raw(
            foundation_terraform_dir, "project_code_ecr_repository_url", env=env
        )
        superset_repository = tools.terraform.output_raw(
            foundation_terraform_dir, "superset_ecr_repository_url", env=env
        )
        return FoundationFacts(
            cluster_name=cluster_name,
            kube_context=cluster_name,
            project_code_repository=project_code_repository,
            superset_repository=superset_repository,
            aws_region=aws_region,
        )

    def cluster_reachable(self, tools: Toolkit, facts: FoundationFacts, *, env: Mapping[str, str]) -> bool:
        return tools.aws.eks_describe_cluster(facts.cluster_name, region=facts.aws_region, env=env, check=False).ok

    def update_kubeconfig(
        self, tools: Toolkit, facts: FoundationFacts, *, kubeconfig_path: Path, env: Mapping[str, str]
    ) -> None:
        tools.aws.eks_update_kubeconfig(
            facts.cluster_name,
            region=facts.aws_region,
            kubeconfig_path=kubeconfig_path,
            alias=facts.cluster_name,
            env=env,
        )

    def registry_login(
        self, tools: Toolkit, facts: FoundationFacts, *, repository: str, env: Mapping[str, str]
    ) -> None:
        registry = repository.split("/", 1)[0]
        password = tools.aws.ecr_get_login_password(region=facts.aws_region, env=env)
        tools.docker.login(registry, username="AWS", password=password, env=env)

    def platform_apply_variables(self, config: CloudDeploymentConfig, facts: FoundationFacts) -> dict[str, str]:
        from olf.deployment.cloud.images import resolve_effective_images

        images = resolve_effective_images(config.images, facts)
        return {
            "namespace": config.namespace,
            "aws_region": facts.aws_region or "",
            "kube_context": facts.kube_context,
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
            "dagster_chart_package_path": str(config.charts.dagster_package_path),
        }

    def platform_destroy_variables(self, config: CloudDeploymentConfig, facts: FoundationFacts) -> dict[str, str]:
        return {
            "namespace": config.namespace,
            "aws_region": facts.aws_region or "",
            "kube_context": facts.kube_context,
            "kubeconfig_path": str(config.paths.kubeconfig_path),
            "foundation_state_path": str(config.paths.foundation_state_path),
        }

    def cleanup_polaris_jobs_before_apply(self) -> bool:
        return False

    def forward_base_targets(self) -> tuple[ForwardTarget, ...]:
        return (ForwardTarget("trino", "svc/trino", 8080, 8080),)

    def artifact_transport(self) -> str:
        return "direct"

    def generate_floe_manifests(
        self,
        config: CloudDeploymentConfig,
        tools: Toolkit,
        *,
        repo_root: Path,
        namespace: str,  # noqa: ARG002 - AWS's Glue profile strategy is namespace-independent
        governance_enabled: bool,  # noqa: ARG002 - AWS's Glue profile strategy is always used
        environ: Mapping[str, str],
        env: Mapping[str, str],
    ) -> list[Path]:
        log.step("Using the AWS Glue Floe profile strategy (one rendered profile per product).")
        return generate_aws_manifests(config.floe, tools, repo_root=repo_root, environ=environ, env=env)
