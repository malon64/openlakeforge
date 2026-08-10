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
import tomllib
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import yaml
from packaging.specifiers import SpecifierSet
from packaging.version import InvalidVersion, Version

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
    """Write a checksums manifest, excluding the manifest's own output path.

    `compute_checksums` only ever excluded the literal name "checksums.txt". A
    custom `output` path inside `directory` therefore got hashed on a rerun
    (its previous content, since the new content hasn't been written yet), and
    the manifest that overwrote it a moment later recorded a checksum for
    bytes that no longer existed there -- `sha256sum -c` fails on that entry
    forever afterward.
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


def render_compatibility_matrix(catalog: dict[str, Any]) -> str:
    """Render the OpenLakeForge <-> Kubernetes <-> Terraform <-> Helm chart
    compatibility matrix as Markdown, sourced entirely from the catalog.

    Always includes COMPATIBILITY_MATRIX_HEADER so the checked-in docs copy
    and _check_compatibility_matrix_up_to_date's comparison can never drift
    from each other by construction -- the header carries the regeneration
    instructions this exact drift previously required someone to remember by
    hand.
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

    lockfiles = terraform.get("lockfiles") or {}
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
            row = [lockfiles.get(root, {}).get(provider, "") for root in roots]
            lines.append(f"| {provider} | " + " | ".join(row) + " |")
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

    return COMPATIBILITY_MATRIX_HEADER + "\n".join(lines)


def _check_compatibility_matrix_up_to_date(repo_root: Path, catalog: dict[str, Any]) -> CheckResult:
    """The checked-in docs/release/compatibility-matrix.md must match a fresh
    render of the catalog exactly.

    Nothing previously enforced this -- the file's own header admitted as
    much -- so a catalog change (e.g. a Terraform provider bump) could ship
    without the published-facing doc ever being regenerated, leaving it
    presenting stale exact-version claims to consumers.
    """
    doc_path = repo_root / "docs/release/compatibility-matrix.md"
    if not doc_path.is_file():
        return CheckResult("compatibility matrix doc is up to date", False, f"{doc_path} does not exist")
    expected = render_compatibility_matrix(catalog)
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


def _check_actions_sha_pinned(repo_root: Path, catalog: dict[str, Any]) -> CheckResult:
    workflows_dir = repo_root / ".github" / "workflows"
    if not workflows_dir.is_dir():
        return CheckResult("workflow actions are SHA-pinned", False, "no .github/workflows directory")

    uses_pattern = re.compile(r"^\s*(?:-\s*)?uses:\s*([^@\s]+)@(\S+)")
    problems: list[str] = []
    # Every occurrence is retained rather than collapsed into a dict keyed by
    # action name: keying by action lets a later workflow's cataloged SHA silently
    # overwrite an earlier workflow's mismatched one, so an uncataloged action ref
    # would pass this gate.
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


def _pep503_normalize(name: str) -> str:
    return re.sub(r"[-_.]+", "-", name).lower()


def _parse_pep508_name_and_constraint(spec: str) -> tuple[str, str]:
    """Split a PEP 508 dependency string into (normalized name, version constraint).

    The constraint is the raw specifier text -- "==1.13.6", ">=1.17,<2", or ""
    when unconstrained -- suitable for packaging.specifiers.SpecifierSet, so a
    range can be validated against a locked version, not just an exact pin.
    """
    match = re.match(r"^([A-Za-z0-9][A-Za-z0-9._-]*)\s*(\[[^\]]*\])?\s*(.*)$", spec.strip())
    if not match:
        return _pep503_normalize(spec.strip()), ""
    name = _pep503_normalize(match.group(1))
    constraint = match.group(3).split(";", 1)[0].strip()  # drop any environment marker
    return name, constraint


def _pyproject_dependencies(pyproject_path: Path) -> list[tuple[str, str]]:
    data = tomllib.loads(pyproject_path.read_text())
    deps = (data.get("project") or {}).get("dependencies") or []
    return [_parse_pep508_name_and_constraint(dep) for dep in deps]


def _locked_version_satisfies(locked_version: str, constraint: str) -> bool:
    """Whether a locked version satisfies a PEP 508 constraint, range or exact.

    A dependency tightened from ">=1.17,<2" to ">=1.30,<2" without regenerating
    the lock previously passed unnoticed: only an exact "==" pin was checked
    against the lock, so a still-in-range-but-now-too-old locked version (e.g.
    1.29.0) was silently accepted even though it violates the new declared
    constraint -- the Dockerfile installs it anyway with --no-deps.
    """
    if not constraint:
        return True
    try:
        return Version(locked_version) in SpecifierSet(constraint)
    except InvalidVersion:
        # A locked version string packaging can't parse as PEP 440 is not this
        # check's concern; presence as a direct dependency was already verified.
        return True


def _requirements_lock_direct_versions(lock_path: Path, *, owner_marker: str) -> dict[str, str]:
    """Map normalized package name -> locked version for packages `uv pip compile`
    marked as directly requested by `owner_marker` (a "# via ... <owner> (<pyproject>)"
    comment), as opposed to pulled in transitively by another package.
    """
    lines = lock_path.read_text().splitlines()
    direct: dict[str, str] = {}
    top_level_re = re.compile(r"^([A-Za-z0-9][A-Za-z0-9._-]*)==([^\s\\]+)")
    i = 0
    while i < len(lines):
        match = top_level_re.match(lines[i])
        if not match:
            i += 1
            continue
        name, version = match.group(1), match.group(2)
        block_end = i + 1
        while block_end < len(lines) and lines[block_end][:1] in (" ", "\t"):
            block_end += 1
        block = "\n".join(lines[i:block_end])
        if f"{owner_marker} (" in block:
            direct[_pep503_normalize(name)] = version
        i = block_end
    return direct


def _uv_lock_requires_dist(uv_lock_path: Path, package_name: str) -> dict[str, str]:
    """Map normalized package name -> declared specifier, from `package_name`'s
    own [[package]] block's [package.metadata].requires-dist -- uv's own
    record of what the project's pyproject.toml currently declares, distinct
    from the resolved transitive closure.
    """
    data = tomllib.loads(uv_lock_path.read_text())
    for package in data.get("package", []):
        if package.get("name") == package_name:
            metadata = package.get("metadata") or {}
            return {
                _pep503_normalize(entry["name"]): (entry.get("specifier") or "").strip()
                for entry in metadata.get("requires-dist", [])
                if "name" in entry
            }
    return {}


def _check_lockfiles_synced_with_pyproject(repo_root: Path, catalog: dict[str, Any]) -> CheckResult:
    """Catch a pyproject.toml dependency added or version-changed without
    regenerating the corresponding lockfile.

    `_check_lockfiles` only verifies the lockfiles exist and are non-empty.
    `check-project-code.sh` and `uv sync` both install straight from
    pyproject.toml metadata, so that drift can pass every other check while
    `images/project-code/Dockerfile`'s `pip install --require-hashes --no-deps
    -r requirements.lock` installs the stale, hash-pinned lock instead --
    silently shipping the wrong (or a missing) runtime dependency.
    """
    python = (catalog.get("components") or {}).get("python") or {}
    problems: list[str] = []

    project_code_pyproject = repo_root / "images/project-code/pyproject.toml"
    project_code_lock_rel = python.get("project_code_lock")
    if project_code_pyproject.is_file() and project_code_lock_rel:
        lock_path = repo_root / project_code_lock_rel
        if lock_path.is_file():
            direct = _requirements_lock_direct_versions(lock_path, owner_marker="openlakeforge-project-code")
            declared = {name for name, _constraint in _pyproject_dependencies(project_code_pyproject)}
            for name, constraint in _pyproject_dependencies(project_code_pyproject):
                if name not in direct:
                    problems.append(
                        f"images/project-code/pyproject.toml declares {name!r} but "
                        f"{project_code_lock_rel} has no direct entry for it -- regenerate the lock"
                    )
                elif not _locked_version_satisfies(direct[name], constraint):
                    problems.append(
                        f"images/project-code/pyproject.toml requires {name!r}{constraint} but "
                        f"{project_code_lock_rel} locks it at {direct[name]!r}, which does not satisfy that "
                        "constraint -- regenerate the lock"
                    )
            # The reverse direction: a dependency removed from pyproject.toml
            # without regenerating the lock leaves an extra direct entry there.
            # The Dockerfile installs the *entire* lock with --no-deps, so the
            # removed package and its transitive closure would still ship in the
            # published image even though nothing declares it anymore.
            for name in sorted(direct):
                if name not in declared:
                    problems.append(
                        f"{project_code_lock_rel} has a direct entry for {name!r} at {direct[name]!r}, but "
                        "images/project-code/pyproject.toml no longer declares it -- regenerate the lock"
                    )

    tooling_pyproject = repo_root / "tools/olf/pyproject.toml"
    tooling_lock_rel = python.get("tooling_lock")
    if tooling_pyproject.is_file() and tooling_lock_rel:
        lock_path = repo_root / tooling_lock_rel
        if lock_path.is_file():
            requires_dist = _uv_lock_requires_dist(lock_path, "openlakeforge-tools")
            tooling_declared = {name for name, _constraint in _pyproject_dependencies(tooling_pyproject)}
            for name, constraint in _pyproject_dependencies(tooling_pyproject):
                if name not in requires_dist:
                    problems.append(
                        f"tools/olf/pyproject.toml declares {name!r} but {tooling_lock_rel} records no "
                        "requires-dist entry for it -- run 'uv lock --project tools/olf'"
                    )
                elif SpecifierSet(requires_dist[name] or "") != SpecifierSet(constraint or ""):
                    problems.append(
                        f"tools/olf/pyproject.toml requires {name!r}{constraint} but {tooling_lock_rel} "
                        f"records the specifier {requires_dist[name]!r} for it -- run "
                        "'uv lock --project tools/olf'"
                    )
            for name in sorted(requires_dist):
                if name not in tooling_declared:
                    problems.append(
                        f"{tooling_lock_rel} records a requires-dist entry for {name!r}, but "
                        "tools/olf/pyproject.toml no longer declares it -- run 'uv lock --project tools/olf'"
                    )

    if problems:
        return CheckResult("lockfiles are synchronized with their pyproject.toml", False, "; ".join(problems))
    return CheckResult("lockfiles are synchronized with their pyproject.toml", True)


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
    """Read provider versions from a Terraform dependency lockfile.

    Terraform lockfiles are HCL rather than TOML. The small, deliberately
    strict parser below only extracts provider blocks and their exact selected
    versions, which are the release inputs represented in the component
    catalog.
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


def _check_terraform_lockfiles_synced_with_catalog(
    repo_root: Path, catalog: dict[str, Any]
) -> CheckResult:
    """Ensure cataloged per-root provider versions match Terraform locks.

    Rendering the compatibility matrix from the catalog alone cannot catch a
    lockfile update that was not copied into the catalog. That would publish a
    stale value as the target's exact provider version.
    """
    terraform = ((catalog.get("components") or {}).get("terraform") or {})
    catalog_lockfiles = terraform.get("lockfiles") or {}
    problems: list[str] = []
    lockfile_root = repo_root / "infra" / "terraform"
    discovered_lockfiles = (
        {path.relative_to(repo_root).as_posix() for path in lockfile_root.rglob(".terraform.lock.hcl")}
        if lockfile_root.is_dir()
        else set()
    )
    cataloged_paths = set(catalog_lockfiles)
    for relative_path in sorted(discovered_lockfiles - cataloged_paths):
        problems.append(f"{relative_path} is not recorded in components.terraform.lockfiles")
    for relative_path, cataloged_versions in sorted(catalog_lockfiles.items()):
        lock_path = repo_root / relative_path
        if not lock_path.is_file():
            problems.append(f"{relative_path} does not exist")
            continue
        actual_versions = _terraform_lock_provider_versions(lock_path)
        expected_versions = cataloged_versions if isinstance(cataloged_versions, dict) else {}
        for provider in sorted(set(expected_versions) | set(actual_versions)):
            expected = expected_versions.get(provider)
            actual = actual_versions.get(provider)
            if expected != actual:
                problems.append(
                    f"{relative_path}: {provider} catalog={expected!r}, lockfile={actual!r}"
                )
    if problems:
        return CheckResult("Terraform lockfiles match the component catalog", False, "; ".join(problems))
    return CheckResult("Terraform lockfiles match the component catalog", True)


def run_release_check(
    repo_root: str | Path = ".",
    *,
    catalog_path: str | Path = DEFAULT_CATALOG_PATH,
    tag: str | None = None,
) -> ReleaseCheckReport:
    """Run the release-readiness / clean-install consistency gate.

    Validates: catalog version matches the tag (when a tag is given), every
    catalog image is digest-pinned, every workflow action is SHA-pinned and
    recorded in the catalog, both lockfiles exist and are non-empty and stay
    synchronized with their pyproject.toml, cataloged Terraform providers
    match per-root lockfiles, and no Dockerfile FROM/ARG is unpinned outside
    of build-arg indirection.
    """
    root = Path(repo_root).resolve()
    catalog = load_catalog(root / catalog_path)

    report = ReleaseCheckReport()
    report.results.append(_check_version_matches_tag(catalog, tag))
    report.results.append(_check_images_digest_pinned(catalog))
    report.results.append(_check_actions_sha_pinned(root, catalog))
    report.results.append(_check_lockfiles(root, catalog))
    report.results.append(_check_lockfiles_synced_with_pyproject(root, catalog))
    report.results.append(_check_dockerfiles_pinned(root))
    report.results.append(_check_terraform_lockfiles_synced_with_catalog(root, catalog))
    report.results.append(_check_compatibility_matrix_up_to_date(root, catalog))
    return report
