"""Typed cloud-provider (AWS/Azure) deployment configuration.

Mirrors `olf.deployment.local.config`: plain frozen dataclasses with
`from_environment` classmethods built on the same `${VAR:-default}`
fallback semantics (`olf.deployment.env_settings`). Only the genuinely
provider-neutral settings live here - project-code/Superset image
selection, Helm chart caching, the platform Terraform var-file/retry
policy, and Floe manifest generation. Foundation-specific settings (AWS
region/node sizing/instance types; Azure node count/ACR name prefix) stay
in `cloud/aws.py`/`cloud/azure.py`, matching the shell scripts' own
provider split.

`Profile` (not the shell's ad hoc `ENABLE_GOVERNANCE`/`ENABLE_ANALYTICS`
variables) drives Full/Slim behavior here too, consistent with the
precedent ADR 0025 set for the local provider.
"""

from __future__ import annotations

import subprocess
from collections.abc import Mapping
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path

from olf.deployment.context import DeploymentContext
from olf.deployment.env_settings import env as _env
from olf.deployment.env_settings import retry_policy as _retry_policy
from olf.deployment.env_settings import truthy as _truthy
from olf.deployment.floe_manifests import FloeManifestSettings
from olf.deployment.retry import RetryPolicy

_DEFAULT_SUPERSET_BASE_IMAGE = (
    "apache/superset:6.1.0@sha256:fb3464528ec7076f91195f0ff7835755aa023e281f1bb78a84782ce7a36b3705"
)
_DEFAULT_PYTHON_BASE_IMAGE_DIGEST = "sha256:57cd7c3a7a273101a6485ba99423ee568157882804b1124b4dd04266317710de"
_DEFAULT_PYTHON_BASE_IMAGE = {
    "aws": f"public.ecr.aws/docker/library/python:3.12-slim@{_DEFAULT_PYTHON_BASE_IMAGE_DIGEST}",
    "azure": f"python:3.12-slim@{_DEFAULT_PYTHON_BASE_IMAGE_DIGEST}",
}


def _git_or_time_tag(repo_root: Path) -> str:
    """Port of `scripts/lib/common.sh::git_or_time_tag`."""
    try:
        result = subprocess.run(
            ["git", "-C", str(repo_root), "rev-parse", "--short", "HEAD"],
            capture_output=True,
            text=True,
            check=True,
        )
        sha = result.stdout.strip()
        if sha:
            return sha
    except (subprocess.CalledProcessError, FileNotFoundError):
        pass
    return datetime.now(UTC).strftime("%Y%m%d%H%M%S")


def default_image_tag(repo_root: Path, *, scope: str) -> str:
    return f"{scope}-{_git_or_time_tag(repo_root)}"


@dataclass(frozen=True)
class CloudImageSettings:
    project_code_repository: str
    project_code_tag: str
    project_code_pull_policy: str
    project_code_revision: str
    project_code_python_base_image: str
    project_code_dbt_profile_env: str
    superset_repository: str
    superset_tag: str
    superset_pull_policy: str
    superset_base_image: str
    image_platform: str
    pull_retry: RetryPolicy
    build_retry: RetryPolicy
    push_retry: RetryPolicy

    @property
    def project_code_image(self) -> str:
        return f"{self.project_code_repository}:{self.project_code_tag}"

    @property
    def superset_image(self) -> str:
        return f"{self.superset_repository}:{self.superset_tag}"

    @classmethod
    def from_environment(cls, environ: Mapping[str, str], *, scope: str, image_tag: str) -> CloudImageSettings:
        """Build settings for `scope` ("aws" or "azure").

        `project_code_repository`/`superset_repository` are empty when not
        explicitly overridden via `PROJECT_CODE_IMAGE_REPOSITORY`/
        `SUPERSET_IMAGE_REPOSITORY` - `CloudProvider` fills in the
        foundation-derived registry (ECR/ACR) once it resolves, mirroring
        the shell scripts' `${VAR:-$(terraform output ...)}` fallback.
        """
        return cls(
            project_code_repository=_env(environ, "PROJECT_CODE_IMAGE_REPOSITORY", ""),
            project_code_tag=_env(environ, "PROJECT_CODE_IMAGE_TAG", image_tag),
            project_code_pull_policy=_env(environ, "PROJECT_CODE_IMAGE_PULL_POLICY", "Always"),
            project_code_revision=_env(environ, "PROJECT_CODE_IMAGE_REVISION", "manual"),
            project_code_python_base_image=_env(
                environ, "PROJECT_CODE_PYTHON_BASE_IMAGE", _DEFAULT_PYTHON_BASE_IMAGE[scope]
            ),
            project_code_dbt_profile_env=_env(environ, "PROJECT_CODE_DBT_PROFILE_ENV", scope),
            superset_repository=_env(environ, "SUPERSET_IMAGE_REPOSITORY", ""),
            superset_tag=_env(environ, "SUPERSET_IMAGE_TAG", image_tag),
            superset_pull_policy=_env(environ, "SUPERSET_IMAGE_PULL_POLICY", "Always"),
            superset_base_image=_env(environ, "SUPERSET_BASE_IMAGE", _DEFAULT_SUPERSET_BASE_IMAGE),
            image_platform=_env(environ, f"{scope.upper()}_IMAGE_PLATFORM", "linux/amd64"),
            pull_retry=_retry_policy(
                environ, specific_attempts="DOCKER_PULL_ATTEMPTS", specific_delay="DOCKER_PULL_RETRY_DELAY_SECONDS"
            ),
            build_retry=_retry_policy(
                environ, specific_attempts="DOCKER_BUILD_ATTEMPTS", specific_delay="DOCKER_BUILD_RETRY_DELAY_SECONDS"
            ),
            push_retry=_retry_policy(
                environ, specific_attempts="DOCKER_PUSH_ATTEMPTS", specific_delay="DOCKER_PUSH_RETRY_DELAY_SECONDS"
            ),
        )


@dataclass(frozen=True)
class CloudChartSettings:
    trino_repository_url: str
    trino_chart_ref: str
    trino_version: str
    trino_package_path: Path
    dagster_repository_url: str
    dagster_chart_ref: str
    dagster_version: str
    dagster_package_path: Path

    @classmethod
    def from_environment(cls, environ: Mapping[str, str], *, helm_cache_dir: Path) -> CloudChartSettings:
        trino_version = _env(environ, "TRINO_CHART_VERSION", "1.42.2")
        dagster_version = _env(environ, "DAGSTER_CHART_VERSION", "1.13.6")
        return cls(
            trino_repository_url=_env(environ, "TRINO_CHART_REPOSITORY", "https://trinodb.github.io/charts"),
            trino_chart_ref="trino/trino",
            trino_version=trino_version,
            trino_package_path=Path(
                _env(environ, "TRINO_CHART_PACKAGE_PATH", str(helm_cache_dir / f"trino-{trino_version}.tgz"))
            ),
            dagster_repository_url=_env(
                environ, "DAGSTER_CHART_REPOSITORY", "https://dagster-io.github.io/helm"
            ),
            dagster_chart_ref="dagster/dagster",
            dagster_version=dagster_version,
            dagster_package_path=Path(
                _env(
                    environ,
                    "DAGSTER_CHART_PACKAGE_PATH",
                    str(helm_cache_dir / f"dagster-{dagster_version}-no-schema.tgz"),
                )
            ),
        )


@dataclass(frozen=True)
class CloudTerraformSettings:
    """The platform Terraform apply's var-file and retry policy.

    Foundation-level tfvars handling (AWS: optional; Azure: required) is a
    genuine provider difference and stays in `cloud/aws.py`/`cloud/azure.py`
    - this only covers the platform apply, which AWS var-files and Azure
    does not (Azure's `stack/platform-up.sh` never references a tfvars
    file at all).
    """

    var_file: Path | None
    apply_retry: RetryPolicy

    @classmethod
    def from_environment(
        cls, environ: Mapping[str, str], *, repo_root: Path, platform_terraform_dir: Path, scope: str
    ) -> CloudTerraformSettings:
        resolved: Path | None = None
        if scope == "aws":
            # AWS's stack/platform-up.sh reuses the same AWS_TFVARS_FILE (mandatory
            # account tags) as the foundation apply - unlike Azure, whose platform
            # Terraform root declares no foundation-only variables at all
            # (resource_group_name, location, node_vm_size, ...) and would fail
            # `-var-file` validation if AZURE_TFVARS_FILE were passed to it.
            raw = environ.get("AWS_TFVARS_FILE") or ""
            if raw:
                candidate = Path(raw)
                resolved = candidate if candidate.is_absolute() else repo_root / candidate
            else:
                default_tfvars = platform_terraform_dir / "sandbox.tfvars"
                resolved = default_tfvars if default_tfvars.is_file() else None
        return cls(
            var_file=resolved,
            apply_retry=RetryPolicy(
                max_attempts=int(_env(environ, f"{scope.upper()}_UP_RETRY_ATTEMPTS", "4")),
                delay_seconds=float(_env(environ, f"{scope.upper()}_UP_RETRY_DELAY_SECONDS", "20.0")),
            ),
        )


@dataclass(frozen=True)
class CloudDeploymentConfig:
    context: DeploymentContext
    images: CloudImageSettings
    charts: CloudChartSettings
    terraform: CloudTerraformSettings
    floe: FloeManifestSettings
    force_foundation_down: bool

    @property
    def paths(self):  # noqa: ANN201 - DeploymentPaths, avoided import cycle noise
        return self.context.paths

    @property
    def namespace(self) -> str:
        return self.context.namespace

    @property
    def kube_context(self) -> str:
        return self.context.kube_context

    @property
    def features(self):  # noqa: ANN201 - DeploymentFeatures
        return self.context.features

    @classmethod
    def from_environment(
        cls,
        environ: Mapping[str, str],
        *,
        context: DeploymentContext,
        var_file: Path | None = None,
    ) -> CloudDeploymentConfig:
        scope = context.provider.value
        repo_root = context.paths.repo_root
        raw_tag = environ.get(f"{scope.upper()}_IMAGE_TAG")
        tag = raw_tag if raw_tag else default_image_tag(repo_root, scope=scope)
        terraform = CloudTerraformSettings.from_environment(
            environ, repo_root=repo_root, platform_terraform_dir=context.paths.platform_terraform_dir, scope=scope
        )
        if var_file is not None:
            terraform = CloudTerraformSettings(var_file=var_file, apply_retry=terraform.apply_retry)
        return cls(
            context=context,
            images=CloudImageSettings.from_environment(environ, scope=scope, image_tag=tag),
            charts=CloudChartSettings.from_environment(environ, helm_cache_dir=context.paths.helm_cache_dir),
            terraform=terraform,
            floe=FloeManifestSettings.from_environment(environ, repo_root=repo_root, scope=scope),
            force_foundation_down=_truthy(_env(environ, f"{scope.upper()}_FOUNDATION_FORCE_DOWN", "false")),
        )
