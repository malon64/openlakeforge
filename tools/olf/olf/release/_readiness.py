"""Release-readiness gate ("olf release check").

Owns the pin/drift checks that catch a release from being cut with an
unpinned image, an un-cataloged workflow action, or Terraform/Helm
inputs that have drifted from `release/component-catalog.yaml`.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import yaml

from olf.release._compatibility import (
    _check_compatibility_matrix_up_to_date,
    _check_provider_using_terraform_roots_have_lockfiles,
    _check_terraform_required_versions_match_catalog,
    _helm_chart_versions_from_terraform_modules,
)
from olf.release._manifest import (
    ACTION_SHA_PATTERN,
    DEFAULT_CATALOG_PATH,
    IMAGE_DIGEST_SUFFIX_PATTERN,
    CheckResult,
    ReleaseCheckReport,
    catalog_version,
    load_catalog,
    tag_for_version,
)

_IMAGE_TAG_DIGEST_PATTERN = re.compile(r"@sha256:[0-9a-f]{64}$")


def _check_version_matches_tag(catalog: dict[str, Any], tag: str | None) -> CheckResult:
    version = catalog_version(catalog)
    if tag is None:
        return CheckResult("catalog version is valid alpha semver", True, version)
    expected_tag = tag_for_version(version)
    if tag != expected_tag:
        return CheckResult(
            "tag matches catalog distribution.version",
            False,
            f"tag {tag!r} does not match expected {expected_tag!r} (catalog version {version!r})",
        )
    return CheckResult("tag matches catalog distribution.version", True, f"{tag} == {expected_tag}")


def _check_images_digest_pinned(catalog: dict[str, Any]) -> CheckResult:
    images = (catalog.get("components") or {}).get("images") or {}
    if not images:
        return CheckResult("catalog images are digest-pinned", False, "no images declared in catalog")
    unpinned = [name for name, ref in images.items() if not IMAGE_DIGEST_SUFFIX_PATTERN.search(str(ref))]
    if unpinned:
        return CheckResult(
            "catalog images are digest-pinned", False, f"missing @sha256 digest: {', '.join(sorted(unpinned))}"
        )
    return CheckResult("catalog images are digest-pinned", True, f"{len(images)} image(s) checked")


@dataclass(frozen=True)
class _ImageDeploymentSource:
    """Registered files that resolve to one or more deployed image refs."""

    paths: tuple[str, ...]
    full_ref_pattern: re.Pattern[str] | None = None
    full_ref_key_path: tuple[str, ...] | None = None
    registry_key_path: tuple[str, ...] | None = None
    repository_key_path: tuple[str, ...] | None = None
    tag_key_path: tuple[str, ...] | None = None


# Every non-build-only catalog image, mapped to exactly where its deployed
# reference lives and how to read it. Terraform embeds the complete
# "repo:tag@digest" as one string. Helm values either embed a full reference
# at one YAML key path or split it across explicit registry, repository, and
# tag keys; split values are reconstructed from deployment fields, never from
# the catalog, so a mirror/repository change is detected as drift too.
#
# A catalog image not covered by this map and not in _BUILD_ONLY_IMAGES fails
# the check outright: matching by loose text search (an earlier version of
# this check) could not tell "not deployed anywhere in this repo" apart from
# "deployed, but the tag changed since this was last searched for" -- a
# version bump made the drift this check exists to catch disappear silently.
_IMAGE_DEPLOYMENT_SOURCES: dict[str, _ImageDeploymentSource] = {
    "k8s_bootstrap": _ImageDeploymentSource(
        paths=(
            "infra/terraform/modules/catalog/polaris/variables.tf",
            "infra/terraform/modules/governance/openmetadata/variables.tf",
        ),
        full_ref_pattern=re.compile(
            r'variable\s+"bootstrap_job_image"\s*\{.*?default\s*=\s*"([^"\n]+@sha256:[0-9a-f]{64})"',
            re.DOTALL,
        ),
    ),
    "opensearch": _ImageDeploymentSource(
        paths=("infra/helm/values/local/openmetadata-deps.yaml",),
        repository_key_path=("opensearch", "image", "repository"),
        tag_key_path=("opensearch", "image", "tag"),
    ),
    "postgres": _ImageDeploymentSource(
        paths=(
            "infra/terraform/modules/storage/postgresql/workload.tf",
            "infra/terraform/modules/storage/postgresql/bootstrap.tf",
            "infra/terraform/modules/storage/rds-postgresql/main.tf",
        ),
        full_ref_pattern=re.compile(r'image\s*=\s*"([^"]+@sha256:[0-9a-f]{64})"'),
    ),
    "seaweedfs": _ImageDeploymentSource(
        paths=("infra/helm/values/local/seaweedfs.yaml",),
        repository_key_path=("image", "repository"),
        tag_key_path=("image", "tag"),
    ),
    "polaris": _ImageDeploymentSource(
        paths=("infra/helm/values/local/polaris.yaml",),
        repository_key_path=("image", "repository"),
        tag_key_path=("image", "tag"),
    ),
    "polaris_admin_tool": _ImageDeploymentSource(
        paths=("infra/terraform/modules/catalog/polaris/variables.tf",),
        full_ref_pattern=re.compile(
            r'variable\s+"metastore_bootstrap_job_image"\s*\{.*?default\s*=\s*"([^"\n]+@sha256:[0-9a-f]{64})"',
            re.DOTALL,
        ),
    ),
    "trino": _ImageDeploymentSource(
        paths=("infra/helm/values/local/trino.yaml",),
        repository_key_path=("image", "repository"),
        tag_key_path=("image", "tag"),
    ),
    "openmetadata_ingestion": _ImageDeploymentSource(
        paths=("infra/helm/values/local/openmetadata.yaml",),
        full_ref_key_path=(
            "openmetadata",
            "config",
            "pipelineServiceClientConfig",
            "k8s",
            "ingestionImage",
        ),
    ),
    "superset_init": _ImageDeploymentSource(
        paths=("infra/helm/values/local/superset.yaml",),
        repository_key_path=("initImage", "repository"),
        tag_key_path=("initImage", "tag"),
    ),
    "superset_redis": _ImageDeploymentSource(
        paths=("infra/helm/values/local/superset.yaml",),
        registry_key_path=("redis", "image", "registry"),
        repository_key_path=("redis", "image", "repository"),
        tag_key_path=("redis", "image", "tag"),
    ),
}
_BUILD_ONLY_IMAGES = frozenset({"project_code_base", "superset_base"})


def _value_at_key_path(data: Any, key_path: tuple[str, ...]) -> Any:
    value = data
    for key in key_path:
        if not isinstance(value, dict) or key not in value:
            return None
        value = value[key]
    return value


def _deployed_image_references(source_path: Path, source: _ImageDeploymentSource) -> list[str]:
    """Read every complete image reference from one registered source file."""
    source_text = source_path.read_text()
    if source.full_ref_pattern is not None:
        return [found.group(1) for found in source.full_ref_pattern.finditer(source_text)]

    try:
        values = yaml.safe_load(source_text)
    except yaml.YAMLError:
        return []

    if source.full_ref_key_path is not None:
        found = _value_at_key_path(values, source.full_ref_key_path)
        return [found] if isinstance(found, str) else []

    if source.repository_key_path is None or source.tag_key_path is None:
        return []
    repository = _value_at_key_path(values, source.repository_key_path)
    tag = _value_at_key_path(values, source.tag_key_path)
    if not isinstance(repository, str) or not isinstance(tag, str):
        return []
    registry_prefix = ""
    if source.registry_key_path is not None:
        registry = _value_at_key_path(values, source.registry_key_path)
        if not isinstance(registry, str) or not registry:
            return []
        registry_prefix = f"{registry.rstrip('/')}/"
    return [f"{registry_prefix}{repository}:{tag}"]


def _check_images_match_deployment_sources(repo_root: Path, catalog: dict[str, Any]) -> CheckResult:
    """Cross-check each catalog image against its registered deployment source.

    _check_images_digest_pinned only validates the catalog's own shape.
    Deployed images are Dockerfile build inputs baked in at build time
    (project_code_base, superset_base -- no independent source to compare
    against) or specific fields in Helm values / Terraform module source, so
    each is checked against exactly where it is actually deployed from.
    """
    images = (catalog.get("components") or {}).get("images") or {}

    problems: list[str] = []
    for name in sorted(set(_IMAGE_DEPLOYMENT_SOURCES) - set(images)):
        problems.append(f"{name}: registered deployment image is missing from the component catalog")

    for name, ref in sorted(images.items()):
        ref = str(ref)
        if not _IMAGE_TAG_DIGEST_PATTERN.search(ref):
            continue  # missing/malformed digest is already caught by _check_images_digest_pinned
        if name in _BUILD_ONLY_IMAGES:
            continue

        source = _IMAGE_DEPLOYMENT_SOURCES.get(name)
        if source is None:
            problems.append(
                f"{name}: no registered deployment source -- add it to _IMAGE_DEPLOYMENT_SOURCES or "
                "_BUILD_ONLY_IMAGES in tools/olf/olf/release/_readiness.py"
            )
            continue

        for relative_path in source.paths:
            source_path = repo_root / relative_path
            if not source_path.is_file():
                problems.append(f"{name}: deployment source {relative_path} does not exist")
                continue

            deployed_references = _deployed_image_references(source_path, source)
            if not deployed_references:
                problems.append(f"{name}: {relative_path} has no complete image reference")
                continue

            for deployed in deployed_references:
                if deployed != ref:
                    problems.append(f"{name}: catalog pins {ref!r} but {relative_path} pins {deployed!r}")

    if problems:
        return CheckResult("catalog images match their deployment sources", False, "; ".join(problems))
    return CheckResult("catalog images match their deployment sources", True)


def _check_actions_sha_pinned(repo_root: Path, catalog: dict[str, Any]) -> CheckResult:
    workflows_dir = repo_root / ".github" / "workflows"
    if not workflows_dir.is_dir():
        return CheckResult("workflow actions are SHA-pinned", False, "no .github/workflows directory")

    uses_pattern = re.compile(r"^\s*(?:-\s*)?uses:\s*([^@\s]+)@(\S+)")
    problems: list[str] = []
    # Every occurrence is kept (not collapsed into a dict keyed by action name)
    # so a mismatch in one workflow can't be hidden by a matching ref in another.
    occurrences: list[tuple[str, str, str]] = []
    for workflow_file in sorted(workflows_dir.glob("*.yml")) + sorted(workflows_dir.glob("*.yaml")):
        for line in workflow_file.read_text().splitlines():
            match = uses_pattern.match(line)
            if not match:
                continue
            action, ref = match.group(1), match.group(2)
            if not ACTION_SHA_PATTERN.match(ref):
                problems.append(f"{workflow_file.name}: {action}@{ref}")
            else:
                occurrences.append((workflow_file.name, action, ref))

    catalog_actions = (catalog.get("components") or {}).get("actions") or {}
    if not isinstance(catalog_actions, dict):
        return CheckResult(
            "workflow actions are recorded in the component catalog",
            False,
            "components.actions must be a mapping",
        )

    missing_from_catalog = sorted(
        {
            f"{workflow_name}: {action}@{ref}"
            for workflow_name, action, ref in occurrences
            if catalog_actions.get(action) != ref
        }
    )
    unused_catalog_actions = sorted(set(catalog_actions) - {action for _, action, _ in occurrences})

    if problems:
        return CheckResult("workflow actions are SHA-pinned", False, "; ".join(problems))
    if missing_from_catalog:
        return CheckResult(
            "workflow actions are recorded in the component catalog",
            False,
            f"not recorded (or mismatched) in components.actions: {', '.join(missing_from_catalog)}",
        )
    if unused_catalog_actions:
        return CheckResult(
            "workflow actions are recorded in the component catalog",
            False,
            f"unused entries in components.actions: {', '.join(unused_catalog_actions)}",
        )
    return CheckResult(
        "workflow actions are SHA-pinned and cataloged",
        True,
        f"{len(occurrences)} action reference(s) checked",
    )


def _check_dockerfiles_pinned(repo_root: Path) -> CheckResult:
    problems: list[str] = []
    for dockerfile in sorted(repo_root.glob("images/*/Dockerfile")):
        for line in dockerfile.read_text().splitlines():
            stripped = line.strip()
            if stripped.startswith("FROM ${"):
                continue
            if stripped.startswith("FROM ") and not IMAGE_DIGEST_SUFFIX_PATTERN.search(stripped):
                problems.append(f"{dockerfile.relative_to(repo_root)}: {stripped}")
            if re.match(r"^ARG\s+[A-Z_]*IMAGE=", stripped) and not IMAGE_DIGEST_SUFFIX_PATTERN.search(stripped):
                problems.append(f"{dockerfile.relative_to(repo_root)}: {stripped}")
    if problems:
        return CheckResult("Dockerfile base images are digest-pinned", False, "; ".join(problems))
    return CheckResult("Dockerfile base images are digest-pinned", True)


@dataclass(frozen=True)
class _DeploymentWrapperChartVersion:
    """Chart version exported by a supported platform deployment wrapper."""

    path: str
    chart: str
    variable: str


_DEPLOYMENT_WRAPPER_CHART_VERSIONS = (
    _DeploymentWrapperChartVersion("tools/olf/olf/deployment/local/config.py", "trino", "TRINO_CHART_VERSION"),
    _DeploymentWrapperChartVersion("tools/olf/olf/deployment/cloud/config.py", "trino", "TRINO_CHART_VERSION"),
    _DeploymentWrapperChartVersion("tools/olf/olf/deployment/cloud/config.py", "dagster", "DAGSTER_CHART_VERSION"),
)


def _wrapper_chart_version_default(source_path: Path, variable: str) -> str | None:
    """Read a provider config's chart-version default.

    Every deployment provider (local since #124, AWS/Azure since #125) reads
    its chart-version default through ``_env(environ, "VAR", "version")``.
    """
    text = source_path.read_text()
    pattern = re.compile(rf'_env\(\s*environ,\s*"{re.escape(variable)}",\s*"(?P<version>[^"]+)"\s*\)')
    match = pattern.search(text)
    return match.group("version") if match else None


def _check_chart_versions_match_deployment_wrappers(repo_root: Path) -> CheckResult:
    """Ensure cached chart packages and Terraform module defaults stay aligned.

    The platform wrappers pass chart package paths to Terraform for Trino and
    Dagster, which bypasses each module's ``helm_release.version`` argument.
    The compatibility matrix is still sourced from the module defaults, so
    every wrapper default must match that matrix value.
    """
    module_versions = _helm_chart_versions_from_terraform_modules(repo_root)
    problems: list[str] = []
    checked = 0
    for source in _DEPLOYMENT_WRAPPER_CHART_VERSIONS:
        source_path = repo_root / source.path
        if not source_path.is_file():
            problems.append(f"{source.path}: deployment wrapper does not exist")
            continue
        wrapper_version = _wrapper_chart_version_default(source_path, source.variable)
        if wrapper_version is None:
            problems.append(f"{source.path}: missing {source.variable} default")
            continue
        module_version = module_versions.get(source.chart)
        if module_version is None:
            problems.append(f"{source.path}: no Terraform chart_version default for {source.chart}")
            continue
        checked += 1
        if wrapper_version != module_version:
            problems.append(
                f"{source.path}: {source.variable}={wrapper_version!r} but "
                f"Terraform {source.chart} chart_version={module_version!r}"
            )
    if problems:
        return CheckResult(
            "Helm chart versions match deployment wrappers", False, "; ".join(problems)
        )
    return CheckResult("Helm chart versions match deployment wrappers", True, f"{checked} wrapper value(s) checked")


def run_release_check(
    repo_root: str | Path = ".",
    *,
    catalog_path: str | Path = DEFAULT_CATALOG_PATH,
    tag: str | None = None,
) -> ReleaseCheckReport:
    """Run the release-readiness / clean-install consistency gate.

    Validates: catalog version matches the tag (when a tag is given), every
    catalog image is digest-pinned and matches its Helm deployment source
    (where one exists), every workflow action is SHA-pinned and recorded in
    the catalog, every Terraform root's required_version matches the catalog,
    provider-using Terraform roots retain their lockfiles, effective wrapper
    chart versions match the rendered Terraform-module values, no Dockerfile
    FROM/ARG is unpinned outside of build-arg indirection, and the
    compatibility matrix doc matches a fresh render.
    Lockfile/pyproject.toml sync is validated separately by
    `scripts/test/check-lockfiles.sh` using `uv` directly.
    """
    root = Path(repo_root).resolve()
    catalog = load_catalog(root / catalog_path)

    report = ReleaseCheckReport()
    report.results.append(_check_version_matches_tag(catalog, tag))
    report.results.append(_check_images_digest_pinned(catalog))
    report.results.append(_check_images_match_deployment_sources(root, catalog))
    report.results.append(_check_actions_sha_pinned(root, catalog))
    report.results.append(_check_dockerfiles_pinned(root))
    report.results.append(_check_terraform_required_versions_match_catalog(root, catalog))
    report.results.append(_check_provider_using_terraform_roots_have_lockfiles(root))
    report.results.append(_check_chart_versions_match_deployment_wrappers(root))
    report.results.append(_check_compatibility_matrix_up_to_date(root, catalog))
    return report
