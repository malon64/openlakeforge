from __future__ import annotations

import re
from pathlib import Path

from olf.deployment.cloud.config import (
    CloudChartSettings,
    CloudDeploymentConfig,
    CloudImageSettings,
    CloudTerraformSettings,
    default_image_tag,
)
from olf.deployment.context import DeploymentContext


def test_aws_image_settings_defaults_to_ecr_public_python_base_image() -> None:
    settings = CloudImageSettings.from_environment({}, scope="aws", image_tag="aws-abc123")

    assert settings.project_code_python_base_image.startswith("public.ecr.aws/docker/library/python:3.12-slim@")
    assert settings.project_code_dbt_profile_env == "aws"
    assert settings.project_code_tag == "aws-abc123"
    assert settings.project_code_pull_policy == "Always"
    assert settings.project_code_repository == ""
    assert settings.image_platform == "linux/amd64"


def test_azure_image_settings_defaults_to_docker_hub_python_base_image() -> None:
    settings = CloudImageSettings.from_environment({}, scope="azure", image_tag="azure-abc123")

    assert settings.project_code_python_base_image.startswith("python:3.12-slim@")
    assert not settings.project_code_python_base_image.startswith("public.ecr.aws")
    assert settings.project_code_dbt_profile_env == "azure"


def test_image_platform_env_var_is_scope_prefixed() -> None:
    aws_settings = CloudImageSettings.from_environment(
        {"AWS_IMAGE_PLATFORM": "linux/arm64"}, scope="aws", image_tag="t"
    )
    azure_settings = CloudImageSettings.from_environment(
        {"AZURE_IMAGE_PLATFORM": "linux/arm64"}, scope="azure", image_tag="t"
    )

    assert aws_settings.image_platform == "linux/arm64"
    assert azure_settings.image_platform == "linux/arm64"


def test_image_settings_repository_overrides_are_honored() -> None:
    settings = CloudImageSettings.from_environment(
        {
            "PROJECT_CODE_IMAGE_REPOSITORY": "123.dkr.ecr.eu-west-1.amazonaws.com/project-code",
            "SUPERSET_IMAGE_REPOSITORY": "123.dkr.ecr.eu-west-1.amazonaws.com/superset",
        },
        scope="aws",
        image_tag="aws-abc123",
    )

    assert settings.project_code_image == "123.dkr.ecr.eu-west-1.amazonaws.com/project-code:aws-abc123"
    assert settings.superset_image == "123.dkr.ecr.eu-west-1.amazonaws.com/superset:aws-abc123"


def test_chart_settings_defaults_trino_and_dagster_package_paths(tmp_path: Path) -> None:
    helm_cache_dir = tmp_path / "helm/aws/charts"

    settings = CloudChartSettings.from_environment({}, helm_cache_dir=helm_cache_dir)

    assert settings.trino_package_path == helm_cache_dir / "trino-1.42.2.tgz"
    assert settings.dagster_package_path == helm_cache_dir / "dagster-1.13.6-no-schema.tgz"
    assert settings.trino_chart_ref == "trino/trino"
    assert settings.dagster_chart_ref == "dagster/dagster"


def test_chart_settings_honors_explicit_package_path_overrides(tmp_path: Path) -> None:
    trino_override = tmp_path / "custom-trino.tgz"
    dagster_override = tmp_path / "custom-dagster.tgz"

    settings = CloudChartSettings.from_environment(
        {
            "TRINO_CHART_PACKAGE_PATH": str(trino_override),
            "DAGSTER_CHART_PACKAGE_PATH": str(dagster_override),
        },
        helm_cache_dir=tmp_path / "helm/aws/charts",
    )

    assert settings.trino_package_path == trino_override
    assert settings.dagster_package_path == dagster_override


def test_aws_terraform_settings_uses_default_tfvars_only_if_it_exists(tmp_path: Path) -> None:
    platform_dir = tmp_path / "infra/terraform/environments/aws-poc"
    platform_dir.mkdir(parents=True)

    absent = CloudTerraformSettings.from_environment(
        {}, repo_root=tmp_path, platform_terraform_dir=platform_dir, scope="aws"
    )
    assert absent.var_file is None

    (platform_dir / "sandbox.tfvars").write_text('region = "eu-west-1"\n')
    present = CloudTerraformSettings.from_environment(
        {}, repo_root=tmp_path, platform_terraform_dir=platform_dir, scope="aws"
    )
    assert present.var_file == platform_dir / "sandbox.tfvars"


def test_azure_terraform_settings_has_no_default_var_file(tmp_path: Path) -> None:
    platform_dir = tmp_path / "infra/terraform/environments/azure-poc"
    platform_dir.mkdir(parents=True)
    (platform_dir / "sandbox.tfvars").write_text("resource_group = \"rg\"\n")

    settings = CloudTerraformSettings.from_environment(
        {}, repo_root=tmp_path, platform_terraform_dir=platform_dir, scope="azure"
    )

    assert settings.var_file is None


def test_terraform_settings_explicit_aws_tfvars_file_override_wins(tmp_path: Path) -> None:
    explicit = tmp_path / "explicit.tfvars"
    explicit.write_text("x = 1\n")

    aws_settings = CloudTerraformSettings.from_environment(
        {"AWS_TFVARS_FILE": str(explicit)}, repo_root=tmp_path, platform_terraform_dir=tmp_path, scope="aws"
    )

    assert aws_settings.var_file == explicit


def test_terraform_settings_ignores_azure_tfvars_file_for_the_platform_apply(tmp_path: Path) -> None:
    """AZURE_TFVARS_FILE is a foundation-only concept (handled by AzureBackend.
    foundation_tfvars_file) - Azure's platform Terraform root declares none of the
    foundation-only variables (resource_group_name, location, node_vm_size, ...) a
    foundation tfvars file sets, so passing it through here would fail `-var-file`
    validation on every azure-up/azure-platform-up/platform teardown.
    """
    foundation_tfvars = tmp_path / "sandbox.tfvars"
    foundation_tfvars.write_text("resource_group_name = \"rg\"\n")

    azure_settings = CloudTerraformSettings.from_environment(
        {"AZURE_TFVARS_FILE": str(foundation_tfvars)},
        repo_root=tmp_path,
        platform_terraform_dir=tmp_path,
        scope="azure",
    )

    assert azure_settings.var_file is None


def test_terraform_settings_apply_retry_defaults_and_scope_prefixed_overrides(tmp_path: Path) -> None:
    default_settings = CloudTerraformSettings.from_environment(
        {}, repo_root=tmp_path, platform_terraform_dir=tmp_path, scope="aws"
    )
    assert default_settings.apply_retry.max_attempts == 4
    assert default_settings.apply_retry.delay_seconds == 20.0

    overridden = CloudTerraformSettings.from_environment(
        {"AWS_UP_RETRY_ATTEMPTS": "7", "AWS_UP_RETRY_DELAY_SECONDS": "5"},
        repo_root=tmp_path,
        platform_terraform_dir=tmp_path,
        scope="aws",
    )
    assert overridden.apply_retry.max_attempts == 7
    assert overridden.apply_retry.delay_seconds == 5.0


def test_default_image_tag_falls_back_to_utc_timestamp_outside_a_git_repo(tmp_path: Path) -> None:
    tag = default_image_tag(tmp_path, scope="aws")

    assert re.fullmatch(r"aws-\d{14}", tag), tag


def test_cloud_deployment_config_from_environment_builds_full_config(tmp_path: Path) -> None:
    context = DeploymentContext.aws(repo_root=tmp_path)

    config = CloudDeploymentConfig.from_environment({"AWS_IMAGE_TAG": "aws-fixed"}, context=context)

    assert config.images.project_code_tag == "aws-fixed"
    assert config.images.superset_tag == "aws-fixed"
    assert config.floe.runtime_artifact_dir == tmp_path / ".tmp/floe-runtime/aws"
    assert config.namespace == context.namespace
    assert config.kube_context == context.kube_context
    assert config.force_foundation_down is False


def test_cloud_deployment_config_var_file_argument_overrides_resolved_default_for_aws(tmp_path: Path) -> None:
    context = DeploymentContext.aws(repo_root=tmp_path)
    explicit = tmp_path / "explicit.tfvars"

    config = CloudDeploymentConfig.from_environment({}, context=context, var_file=explicit)

    assert config.terraform.var_file == explicit
    assert config.terraform.foundation_var_file == explicit


def test_cloud_deployment_config_var_file_argument_never_reaches_azure_platform_apply(tmp_path: Path) -> None:
    """An explicit `--var-file` on Azure must route only through
    `foundation_var_file`, never `var_file` - `var_file` also feeds the
    platform apply (`cloud/platform.py`), and Azure's platform Terraform
    root rejects a tfvars file entirely (ADR 0027, binding requirement).
    A combined `olf deploy --provider azure --var-file <foundation.tfvars>`
    run must not fail Terraform `-var-file` validation at the platform
    phase.
    """
    context = DeploymentContext.azure(repo_root=tmp_path)
    explicit = tmp_path / "explicit.tfvars"

    config = CloudDeploymentConfig.from_environment({}, context=context, var_file=explicit)

    assert config.terraform.var_file is None
    assert config.terraform.foundation_var_file == explicit
