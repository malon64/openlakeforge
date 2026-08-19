from __future__ import annotations

from pathlib import Path

from olf.deployment.context import DeploymentContext, Profile
from olf.deployment.local.config import LocalDeploymentConfig


def test_full_profile_defaults(tmp_path: Path) -> None:
    context = DeploymentContext.local(repo_root=tmp_path, profile=Profile.FULL)

    config = LocalDeploymentConfig.from_environment({}, context=context)

    assert config.cluster.name == "openlakeforge-local"
    assert config.cluster.config_path == tmp_path / "infra/kind/local/kind-cluster.yaml"
    assert config.cluster.wait_timeout == "120s"
    assert config.cluster.reset_existing is False
    assert config.images.project_code_image == "ghcr.io/openlakeforge/project-code:local"
    assert config.images.superset_image == "ghcr.io/openlakeforge/superset:local"
    assert config.images.project_code_pull_policy == "Never"
    assert config.charts.trino_version == "1.42.2"
    assert config.charts.trino_package_path == context.paths.helm_cache_dir / "trino-1.42.2.tgz"
    assert config.terraform.var_file is None
    assert config.terraform.apply_retry.max_attempts == 4
    assert config.terraform.apply_retry.delay_seconds == 20.0
    assert config.prefetch.pull_retry.max_attempts == 5
    assert config.prefetch.pull_retry.delay_seconds == 20.0
    assert config.floe.version == "0.6.11"
    assert config.floe.image == "ghcr.io/malon64/floe:0.6.11"
    assert config.floe.runtime_artifact_dir == tmp_path / ".tmp/floe-runtime/local"
    assert config.force_foundation_down is False
    assert config.features.governance_enabled is True
    assert config.features.analytics_enabled is True


def test_slim_profile_defaults_the_tfvars_file(tmp_path: Path) -> None:
    context = DeploymentContext.local(repo_root=tmp_path, profile=Profile.SLIM)

    config = LocalDeploymentConfig.from_environment({}, context=context)

    assert config.terraform.var_file == tmp_path / "infra/terraform/environments/local/slim.tfvars"
    assert config.features.governance_enabled is False
    assert config.features.analytics_enabled is False


def test_explicit_var_file_overrides_slim_default(tmp_path: Path) -> None:
    context = DeploymentContext.local(repo_root=tmp_path, profile=Profile.SLIM)

    config = LocalDeploymentConfig.from_environment({}, context=context, var_file=Path("custom.tfvars"))

    assert config.terraform.var_file == tmp_path / "custom.tfvars"


def test_environment_overrides_are_honored(tmp_path: Path) -> None:
    context = DeploymentContext.local(repo_root=tmp_path)
    environ = {
        "PROJECT_CODE_IMAGE_REPOSITORY": "example.com/project-code",
        "PROJECT_CODE_IMAGE_TAG": "v1",
        "LOCAL_FOUNDATION_RESET": "true",
        "LOCAL_FOUNDATION_FORCE_DOWN": "true",
        "LOCAL_UP_RETRY_ATTEMPTS": "2",
        "LOCAL_UP_RETRY_DELAY_SECONDS": "5",
        "DOCKER_PULL_ATTEMPTS": "9",
    }

    config = LocalDeploymentConfig.from_environment(environ, context=context)

    assert config.images.project_code_image == "example.com/project-code:v1"
    assert config.cluster.reset_existing is True
    assert config.force_foundation_down is True
    assert config.terraform.apply_retry.max_attempts == 2
    assert config.terraform.apply_retry.delay_seconds == 5.0
    assert config.images.pull_retry.max_attempts == 9


def test_docker_retry_fallback_chain(tmp_path: Path) -> None:
    context = DeploymentContext.local(repo_root=tmp_path)

    default_config = LocalDeploymentConfig.from_environment({}, context=context)
    assert default_config.images.pull_retry.max_attempts == 3
    assert default_config.images.pull_retry.delay_seconds == 10.0

    generic_config = LocalDeploymentConfig.from_environment(
        {"DOCKER_REGISTRY_ATTEMPTS": "7", "DOCKER_REGISTRY_RETRY_DELAY_SECONDS": "3"}, context=context
    )
    assert generic_config.images.pull_retry.max_attempts == 7
    assert generic_config.images.pull_retry.delay_seconds == 3.0

    specific_config = LocalDeploymentConfig.from_environment(
        {"DOCKER_REGISTRY_ATTEMPTS": "7", "DOCKER_PULL_ATTEMPTS": "11"}, context=context
    )
    assert specific_config.images.pull_retry.max_attempts == 11
