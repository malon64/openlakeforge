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
    entries = compute_checksums(directory)
    out_path = Path(output) if output else Path(directory) / "checksums.txt"
    out_path.write_text(render_checksums(entries))
    return out_path


def render_compatibility_matrix(catalog: dict[str, Any]) -> str:
    """Render the OpenLakeForge <-> Kubernetes <-> Terraform <-> Helm chart
    compatibility matrix as Markdown, sourced entirely from the catalog.
    """
    components = catalog.get("components") or {}
    distribution = catalog.get("distribution") or {}
    version = distribution.get("version", "unknown")
    terraform = components.get("terraform") or {}
    helm = components.get("helm") or {}
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
    lines.append("| Provider | Version |")
    lines.append("| --- | --- |")
    for provider, provider_version in sorted((terraform.get("providers") or {}).items()):
        lines.append(f"| {provider} | {provider_version} |")
    lines.append("")

    lines.append("## Helm charts")
    lines.append("")
    lines.append("| Chart | Version |")
    lines.append("| --- | --- |")
    for chart, chart_version in sorted(helm.items()):
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

    return "\n".join(lines)


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


def _check_actions_sha_pinned(repo_root: Path, catalog: dict[str, Any]) -> CheckResult:
    workflows_dir = repo_root / ".github" / "workflows"
    if not workflows_dir.is_dir():
        return CheckResult("workflow actions are SHA-pinned", False, "no .github/workflows directory")

    uses_pattern = re.compile(r"^\s*(?:-\s*)?uses:\s*([^@\s]+)@(\S+)")
    problems: list[str] = []
    seen_actions: dict[str, str] = {}
    for workflow_file in sorted(workflows_dir.glob("*.yml")) + sorted(workflows_dir.glob("*.yaml")):
        for line in workflow_file.read_text().splitlines():
            match = uses_pattern.match(line)
            if not match:
                continue
            action, ref = match.group(1), match.group(2)
            if not ACTION_SHA_PATTERN.match(ref):
                problems.append(f"{workflow_file.name}: {action}@{ref}")
            else:
                seen_actions[action] = ref

    catalog_actions = (catalog.get("components") or {}).get("actions") or {}
    missing_from_catalog = [
        f"{action}@{ref}" for action, ref in seen_actions.items() if catalog_actions.get(action) != ref
    ]

    if problems:
        return CheckResult("workflow actions are SHA-pinned", False, "; ".join(problems))
    if missing_from_catalog:
        return CheckResult(
            "workflow actions are recorded in the component catalog",
            False,
            f"not recorded (or mismatched) in components.actions: {', '.join(sorted(missing_from_catalog))}",
        )
    return CheckResult(
        "workflow actions are SHA-pinned and cataloged", True, f"{len(seen_actions)} action(s) checked"
    )


def _check_lockfiles(repo_root: Path, catalog: dict[str, Any]) -> CheckResult:
    python = (catalog.get("components") or {}).get("python") or {}
    problems: list[str] = []
    for key in ("project_code_lock", "tooling_lock"):
        relative = python.get(key)
        if not relative:
            problems.append(f"components.python.{key} missing from catalog")
            continue
        path = repo_root / relative
        if not path.is_file() or path.stat().st_size == 0:
            problems.append(f"{relative} missing or empty")
    if problems:
        return CheckResult("Python lockfiles present and non-empty", False, "; ".join(problems))
    return CheckResult("Python lockfiles present and non-empty", True)


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


def run_release_check(
    repo_root: str | Path = ".",
    *,
    catalog_path: str | Path = DEFAULT_CATALOG_PATH,
    tag: str | None = None,
) -> ReleaseCheckReport:
    """Run the release-readiness / clean-install consistency gate.

    Validates: catalog version matches the tag (when a tag is given), every
    catalog image is digest-pinned, every workflow action is SHA-pinned and
    recorded in the catalog, both lockfiles exist and are non-empty, and no
    Dockerfile FROM/ARG is unpinned outside of build-arg indirection.
    """
    root = Path(repo_root).resolve()
    catalog = load_catalog(root / catalog_path)

    report = ReleaseCheckReport()
    report.results.append(_check_version_matches_tag(catalog, tag))
    report.results.append(_check_images_digest_pinned(catalog))
    report.results.append(_check_actions_sha_pinned(root, catalog))
    report.results.append(_check_lockfiles(root, catalog))
    report.results.append(_check_dockerfiles_pinned(root))
    return report
