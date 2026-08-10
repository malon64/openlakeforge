"""Release-bundle logic: component manifest, checksums, compatibility matrix,
and the release-readiness gate.

Shell/GitHub Actions remain the orchestrators for git, docker, cosign, and
syft invocations (ADR 0017); this module owns the cross-environment logic
that turns `release/component-catalog.yaml` plus resolved image digests into
the artifacts a consumer needs to verify a tagged release: the component
manifest, `checksums.txt`, the compatibility matrix, and the release-check
gate that catches drift before a tag is cut.
"""

from __future__ import annotations

import hashlib
import json
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import yaml

DEFAULT_CATALOG_PATH = "release/component-catalog.yaml"
VERSION_PATTERN = re.compile(r"^\d+\.\d+\.\d+-alpha\.\d+$")
SHA256_DIGEST_PATTERN = re.compile(r"^sha256:[0-9a-f]{64}$")
ACTION_SHA_PATTERN = re.compile(r"^[0-9a-f]{40}$")
IMAGE_DIGEST_SUFFIX_PATTERN = re.compile(r"@sha256:[0-9a-f]{64}$")


class ReleaseError(ValueError):
    """Raised when release inputs are missing, inconsistent, or unpinned."""


@dataclass
class CheckResult:
    """Outcome of a single release-readiness gate check."""

    name: str
    ok: bool
    detail: str = ""


@dataclass
class ReleaseCheckReport:
    """Aggregate result of `olf release check`."""

    results: list[CheckResult] = field(default_factory=list)

    @property
    def ok(self) -> bool:
        return all(result.ok for result in self.results)

    def render(self) -> str:
        lines = []
        for result in self.results:
            status = "PASS" if result.ok else "FAIL"
            detail = f": {result.detail}" if result.detail else ""
            lines.append(f"[{status}] {result.name}{detail}")
        return "\n".join(lines)


def load_catalog(catalog_path: str | Path = DEFAULT_CATALOG_PATH) -> dict[str, Any]:
    """Load and minimally validate the component catalog."""
    path = Path(catalog_path)
    if not path.is_file():
        raise ReleaseError(f"component catalog not found at {path}")
    data = yaml.safe_load(path.read_text())
    if not isinstance(data, dict):
        raise ReleaseError(f"component catalog at {path} did not parse to a mapping")
    if data.get("kind") != "ComponentCatalog":
        raise ReleaseError(f"component catalog at {path} is missing kind: ComponentCatalog")
    distribution = data.get("distribution") or {}
    version = distribution.get("version")
    if not isinstance(version, str) or not VERSION_PATTERN.match(version):
        raise ReleaseError(
            f"component catalog distribution.version {version!r} does not match "
            f"the expected alpha semver pattern (e.g. 0.1.0-alpha.1)"
        )
    return data


def catalog_version(catalog: dict[str, Any]) -> str:
    return str(catalog["distribution"]["version"])


def tag_for_version(version: str) -> str:
    return f"v{version}"


def version_for_tag(tag: str) -> str:
    return tag[1:] if tag.startswith("v") else tag


def build_manifest(
    catalog: dict[str, Any],
    *,
    git_sha: str,
    image_digests: dict[str, str] | None = None,
    generated_at: str | None = None,
) -> dict[str, Any]:
    """Build the resolved component manifest: catalog + resolved image digests + git SHA.

    `image_digests` maps image name (e.g. "project-code", "superset") to a
    fully qualified `repo@sha256:...` reference resolved at build time.
    """
    manifest: dict[str, Any] = {
        "apiVersion": "openlakeforge.io/v1alpha1",
        "kind": "ReleaseManifest",
        "distribution": {
            "version": catalog_version(catalog),
            "tag": tag_for_version(catalog_version(catalog)),
            "git_sha": git_sha,
        },
        "catalog": catalog,
        "resolved_images": dict(sorted((image_digests or {}).items())),
    }
    if generated_at:
        manifest["generated_at"] = generated_at
    return manifest


def render_manifest(manifest: dict[str, Any], fmt: str = "json") -> str:
    if fmt == "json":
        return json.dumps(manifest, indent=2, sort_keys=False) + "\n"
    if fmt == "yaml":
        return yaml.safe_dump(manifest, sort_keys=False, default_flow_style=False)
    raise ReleaseError(f"unsupported manifest format: {fmt!r} (expected 'json' or 'yaml')")


def compute_checksums(directory: str | Path, *, exclude: set[str] | None = None) -> list[tuple[str, str]]:
    """Compute sha256 checksums for every file in `directory` (non-recursive top level,
    recursive into subdirectories), returned as (relative_posix_path, hexdigest) sorted
    by path for determinism. `checksums.txt` itself is always excluded.
    """
    root = Path(directory)
    if not root.is_dir():
        raise ReleaseError(f"checksum directory not found: {root}")
    exclude = {"checksums.txt"} | (exclude or set())
    entries: list[tuple[str, str]] = []
    for path in sorted(root.rglob("*")):
        if not path.is_file():
            continue
        rel = path.relative_to(root).as_posix()
        if rel in exclude:
            continue
        digest = hashlib.sha256(path.read_bytes()).hexdigest()
        entries.append((rel, digest))
    return sorted(entries, key=lambda item: item[0])


def render_checksums(entries: list[tuple[str, str]]) -> str:
    # sha256sum -c compatible: "<digest>  <path>\n"
    return "".join(f"{digest}  {path}\n" for path, digest in entries)


def write_checksums(directory: str | Path, output: str | Path | None = None) -> Path:
    """Write a checksums manifest, excluding the manifest's own output path
    (even a custom one inside `directory`) so a rerun never hashes its own
    prior contents.
    """
    root = Path(directory)
    out_path = Path(output) if output else root / "checksums.txt"
    exclude: set[str] = set()
    try:
        exclude = {out_path.resolve().relative_to(root.resolve()).as_posix()}
    except ValueError:
        pass  # out_path is outside directory; nothing of its own to exclude.
    entries = compute_checksums(root, exclude=exclude)
    out_path.write_text(render_checksums(entries))
    return out_path


COMPATIBILITY_MATRIX_HEADER = """<!--
This file is generated from release/component-catalog.yaml. Do not hand-edit
the tables below -- regenerate with:

    olf release compatibility-matrix --output docs/release/compatibility-matrix.md

(or `uv run --project tools/olf olf release compatibility-matrix --output ...`
from the repo root). The same command produces the copy embedded in every
release bundle by .github/workflows/release.yml, so this file always matches
what a tagged release publishes as of the last catalog update. `make
release-check` fails if this checked-in file drifts from a fresh render;
regenerate it whenever release/component-catalog.yaml changes.
-->

"""


def render_compatibility_matrix(catalog: dict[str, Any], repo_root: str | Path = ".") -> str:
    """Render the OpenLakeForge <-> Kubernetes <-> Terraform <-> Helm chart
    compatibility matrix as Markdown.

    Every table is sourced from the catalog except "Terraform providers by
    root", which is read directly from each `.terraform.lock.hcl` under
    `infra/terraform` -- the exact version a consumer of that root actually
    gets, with no intermediate copy to fall out of sync. Likewise "Helm
    charts" is read from each Terraform module's own `chart_version`
    variable default, not a cataloged copy.

    Always includes COMPATIBILITY_MATRIX_HEADER so the checked-in docs copy
    and _check_compatibility_matrix_up_to_date's comparison stay identical by
    construction.
    """
    root = Path(repo_root)
    components = catalog.get("components") or {}
    distribution = catalog.get("distribution") or {}
    version = distribution.get("version", "unknown")
    terraform = components.get("terraform") or {}
    images = components.get("images") or {}

    lines: list[str] = []
    lines.append(f"# OpenLakeForge {version} compatibility matrix")
    lines.append("")
    lines.append(
        "Generated from `release/component-catalog.yaml`. Every version below is "
        "the exact input pinned for this release; see "
        "[docs/release/component-catalog.md](component-catalog.md) for the update process."
    )
    lines.append("")

    lines.append("## Platform")
    lines.append("")
    lines.append("| Component | Required version |")
    lines.append("| --- | --- |")
    lines.append(f"| Terraform | {terraform.get('required_version', 'unknown')} |")
    lines.append("")

    lines.append("## Terraform providers")
    lines.append("")
    lines.append(
        "The tracked/approved version for each provider. Individual Terraform "
        "roots can lock an older compatible version (below) -- consult that "
        "table for the exact version actually applied to a given target."
    )
    lines.append("")
    lines.append("| Provider | Tracked version |")
    lines.append("| --- | --- |")
    for provider, provider_version in sorted((terraform.get("providers") or {}).items()):
        lines.append(f"| {provider} | {provider_version} |")
    lines.append("")

    terraform_root = root / "infra" / "terraform"
    lockfile_paths = sorted(terraform_root.rglob(".terraform.lock.hcl")) if terraform_root.is_dir() else []
    lockfiles = {
        path.relative_to(root).as_posix(): _terraform_lock_provider_versions(path) for path in lockfile_paths
    }
    if lockfiles:
        lines.append("### Terraform providers by root")
        lines.append("")
        lines.append(
            "The exact version `.terraform.lock.hcl` pins for each root -- what a "
            "consumer of that target actually gets, not the tracked version above."
        )
        lines.append("")
        roots = sorted(lockfiles)
        providers = sorted({provider for root_locks in lockfiles.values() for provider in root_locks})
        lines.append("| Provider | " + " | ".join(roots) + " |")
        lines.append("| --- | " + " | ".join("---" for _ in roots) + " |")
        for provider in providers:
            row = [lockfiles.get(root_path, {}).get(provider, "") for root_path in roots]
            lines.append(f"| {provider} | " + " | ".join(row) + " |")
        lines.append("")

    lines.append("## Helm charts")
    lines.append("")
    lines.append("| Chart | Version |")
    lines.append("| --- | --- |")
    for chart, chart_version in sorted(_helm_chart_versions_from_terraform_modules(root).items()):
        lines.append(f"| {chart} | {chart_version} |")
    lines.append("")

    lines.append("## Container images")
    lines.append("")
    lines.append("| Image | Reference |")
    lines.append("| --- | --- |")
    for name, reference in sorted(images.items()):
        lines.append(f"| {name} | `{reference}` |")
    lines.append("")

    lines.append("## Cloud services (deployment targets)")
    lines.append("")
    lines.append("| Target | Kubernetes foundation | Object storage | Catalog | Managed database |")
    lines.append("| --- | --- | --- | --- | --- |")
    lines.append("| Local | kind | SeaweedFS (in-cluster) | Polaris (in-cluster) | PostgreSQL (in-cluster) |")
    lines.append("| Azure POC | AKS | SeaweedFS (in-cluster) | Polaris (in-cluster) | PostgreSQL (in-cluster) |")
    lines.append("| AWS POC | EKS | S3 | AWS Glue | RDS PostgreSQL |")
    lines.append("")

    lines.append("## Supported upgrade paths")
    lines.append("")
    lines.append(
        "OpenLakeForge is in the Alpha lifecycle stage (see "
        "[docs/industrialization-roadmap.md](../industrialization-roadmap.md), "
        '"Lifecycle Definitions"): breaking changes are allowed between alpha '
        "releases, with migration notes published in `CHANGELOG.md` for every "
        "tag. Until Beta, only the latest alpha tag is maintained; there is no "
        "supported upgrade path guarantee prior to `v0.1.0-alpha.1`."
    )
    lines.append("")

    return COMPATIBILITY_MATRIX_HEADER + "\n".join(lines)


def _check_compatibility_matrix_up_to_date(repo_root: Path, catalog: dict[str, Any]) -> CheckResult:
    """The checked-in docs/release/compatibility-matrix.md must match a fresh
    render of the catalog exactly.
    """
    doc_path = repo_root / "docs/release/compatibility-matrix.md"
    if not doc_path.is_file():
        return CheckResult("compatibility matrix doc is up to date", False, f"{doc_path} does not exist")
    expected = render_compatibility_matrix(catalog, repo_root)
    actual = doc_path.read_text()
    if actual != expected:
        return CheckResult(
            "compatibility matrix doc is up to date",
            False,
            "docs/release/compatibility-matrix.md does not match a fresh render of release/component-catalog.yaml "
            "-- regenerate with 'olf release compatibility-matrix --output docs/release/compatibility-matrix.md'",
        )
    return CheckResult("compatibility matrix doc is up to date", True)


# ---------------------------------------------------------------------------
# Release-readiness gate ("olf release check")
# ---------------------------------------------------------------------------


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


_IMAGE_TAG_DIGEST_PATTERN = re.compile(r"@sha256:[0-9a-f]{64}$")


@dataclass(frozen=True)
class _ImageDeploymentSource:
    """Registered files that resolve to one or more deployed image refs."""

    paths: tuple[str, ...]
    full_ref_pattern: re.Pattern[str] | None = None
    full_ref_key_path: tuple[str, ...] | None = None
    repository_key_path: tuple[str, ...] | None = None
    tag_key_path: tuple[str, ...] | None = None


# Every non-build-only catalog image, mapped to exactly where its deployed
# reference lives and how to read it. Terraform embeds the complete
# "repo:tag@digest" as one string. Helm values either embed a full reference
# at one YAML key path or split it across explicit repository and tag keys;
# split values are reconstructed from both deployment fields, never from the
# catalog, so a mirror/repository change is detected as drift too.
#
# A catalog image not covered by this map and not in _BUILD_ONLY_IMAGES fails
# the check outright: matching by loose text search (an earlier version of
# this check) could not tell "not deployed anywhere in this repo" apart from
# "deployed, but the tag changed since this was last searched for" -- a
# version bump made the drift this check exists to catch disappear silently.
_IMAGE_DEPLOYMENT_SOURCES: dict[str, _ImageDeploymentSource] = {
    "postgres": _ImageDeploymentSource(
        paths=(
            "infra/terraform/modules/storage/postgresql/main.tf",
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
    return [f"{repository}:{tag}"]


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
                "_BUILD_ONLY_IMAGES in tools/olf/olf/release.py"
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
    missing_from_catalog = sorted(
        {
            f"{workflow_name}: {action}@{ref}"
            for workflow_name, action, ref in occurrences
            if catalog_actions.get(action) != ref
        }
    )

    if problems:
        return CheckResult("workflow actions are SHA-pinned", False, "; ".join(problems))
    if missing_from_catalog:
        return CheckResult(
            "workflow actions are recorded in the component catalog",
            False,
            f"not recorded (or mismatched) in components.actions: {', '.join(missing_from_catalog)}",
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


def _terraform_lock_provider_versions(lock_path: Path) -> dict[str, str]:
    """Read provider name -> exact selected version from a `.terraform.lock.hcl`.

    Terraform lockfiles are HCL rather than TOML, so this parses just the
    `provider "<source>" { version = "<v>" }` blocks the compatibility matrix
    needs.
    """
    provider_blocks = re.finditer(
        r'^provider\s+"(?P<provider>[^"]+)"\s*\{(?P<body>.*?)^\}',
        lock_path.read_text(),
        re.MULTILINE | re.DOTALL,
    )
    versions: dict[str, str] = {}
    for block in provider_blocks:
        version = re.search(r'^\s*version\s*=\s*"(?P<version>[^"]+)"\s*$', block.group("body"), re.MULTILINE)
        if version is not None:
            provider = block.group("provider").removeprefix("registry.terraform.io/")
            versions[provider] = version.group("version")
    return versions


def _helm_chart_versions_from_terraform_modules(repo_root: Path) -> dict[str, str]:
    """Read Helm chart name -> version from each Terraform module's own
    `chart_version` variable default under infra/terraform/modules, keyed by
    the module directory name (e.g. .../query/trino -> "trino"). A module
    with a `deps_chart_version` variable (governance/openmetadata's paired
    dependencies chart) also contributes "<module>-dependencies".
    """
    variable_blocks = re.compile(r'variable\s+"(?P<name>[^"]+)"\s*\{(?P<body>.*?)^\}', re.MULTILINE | re.DOTALL)
    default_value = re.compile(r'^\s*default\s*=\s*"(?P<default>[^"]+)"\s*$', re.MULTILINE)

    versions: dict[str, str] = {}
    modules_root = repo_root / "infra" / "terraform" / "modules"
    if not modules_root.is_dir():
        return versions
    for variables_file in sorted(modules_root.rglob("variables.tf")):
        module_name = variables_file.parent.name
        text = variables_file.read_text()
        for block in variable_blocks.finditer(text):
            if block.group("name") not in ("chart_version", "deps_chart_version"):
                continue
            default = default_value.search(block.group("body"))
            if default is None:
                continue
            key = module_name if block.group("name") == "chart_version" else f"{module_name}-dependencies"
            versions[key] = default.group("default")
    return versions


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
    the catalog, no Dockerfile FROM/ARG is unpinned outside of build-arg
    indirection, and the compatibility matrix doc matches a fresh render.
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
    report.results.append(_check_compatibility_matrix_up_to_date(root, catalog))
    return report
