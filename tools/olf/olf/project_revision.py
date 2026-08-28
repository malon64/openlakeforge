"""Build, publish, and verify the immutable `ProjectRevision` (#154).

A project revision identifies the complete promotable data project by
content: descriptors, Floe contracts, dbt, Dagster orchestration code, report
assets when present, the project-code image digest, and the distribution
version. It intentionally excludes `openlakeforge.yaml` (deployment intent,
not project content) and every stage-rendered Floe artifact (manifests,
profiles) -- those embed physical bucket names, namespaces, and secret
references and are regenerated per stage by #115. See ADR 0012.

Unlike `olf.revision` (the v0.2 Floe runtime-artifact revision, now under
`olf floe revision`), this revision never touches a rendered Floe manifest --
only the checked-in domain contract that produces one.
"""

from __future__ import annotations

import hashlib
import json
import re
from collections.abc import Callable, Iterable
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from olf import revision as floe_revision
from olf.artifact_store import RevisionStore, publish_immutable, read_required
from olf.project import ProjectSpec, validate_project

MANIFEST_API_VERSION = "openlakeforge.io/v1alpha1"
MANIFEST_KIND = "ProjectRevision"
REVISION_PREFIX = "project/revisions"
SIDECAR_NAME = "PROJECT-REVISION.json"

_EXCLUDED_DIR_NAMES = frozenset({"target", "dbt_packages", "__pycache__", ".venv", ".pytest_cache"})
_DIGEST_TAG_PATTERN = re.compile(r"@sha256:[0-9a-f]{64}$")
_FORBIDDEN_VALUE_PATTERNS = (
    re.compile(r"s3://", re.IGNORECASE),
    re.compile(r"https?://"),
    re.compile(r"\bAWS_[A-Z_]+"),
    re.compile(r"[A-Z_]*SECRET[A-Z_]*", re.IGNORECASE),
)


class ProjectRevisionError(RuntimeError):
    """A project revision cannot be built, published, or verified safely."""


@dataclass(frozen=True)
class ComponentEntries:
    """One named component of the project and the content digest of each of its files."""

    name: str
    entries: dict[str, str]

    def to_dict(self) -> dict[str, object]:
        return {"name": self.name, "entries": dict(sorted(self.entries.items()))}

    @classmethod
    def from_dict(cls, payload: Any) -> ComponentEntries:
        if (
            not isinstance(payload, dict)
            or not isinstance(payload.get("name"), str)
            or not isinstance(payload.get("entries"), dict)
            or not all(
                isinstance(key, str) and isinstance(digest, str) for key, digest in payload["entries"].items()
            )
        ):
            raise ProjectRevisionError(f"malformed component entry: {payload!r}")
        return cls(name=payload["name"], entries=dict(payload["entries"]))


@dataclass(frozen=True)
class ProjectRevisionManifest:
    """The frozen, content-addressed identity of one complete data project."""

    project_name: str
    distribution_version: str
    project_code_image: str
    components: tuple[ComponentEntries, ...]
    revision: str

    def to_json(self) -> str:
        return (
            json.dumps(
                {
                    "apiVersion": MANIFEST_API_VERSION,
                    "kind": MANIFEST_KIND,
                    "schema_version": 1,
                    "project_name": self.project_name,
                    "distribution_version": self.distribution_version,
                    "project_code_image": self.project_code_image,
                    "components": [component.to_dict() for component in self.components],
                    "revision": self.revision,
                },
                indent=2,
                sort_keys=True,
            )
            + "\n"
        )

    @classmethod
    def from_json(cls, value: str) -> ProjectRevisionManifest:
        try:
            payload = json.loads(value)
        except json.JSONDecodeError as exc:
            raise ProjectRevisionError(f"malformed {SIDECAR_NAME}: {exc}") from exc
        if not isinstance(payload, dict):
            raise ProjectRevisionError(f"malformed {SIDECAR_NAME}: expected a JSON object")
        if payload.get("apiVersion") != MANIFEST_API_VERSION or payload.get("kind") != MANIFEST_KIND:
            raise ProjectRevisionError(
                f"unsupported {SIDECAR_NAME} apiVersion/kind: "
                f"{payload.get('apiVersion')!r}/{payload.get('kind')!r}"
            )
        try:
            manifest = cls(
                project_name=payload["project_name"],
                distribution_version=payload["distribution_version"],
                project_code_image=payload["project_code_image"],
                components=tuple(ComponentEntries.from_dict(item) for item in payload["components"]),
                revision=payload["revision"],
            )
        except KeyError as exc:
            raise ProjectRevisionError(f"malformed {SIDECAR_NAME}: missing {exc}") from exc
        manifest.validate()
        return manifest

    def validate(self) -> None:
        actual = _aggregate_components(self.components)
        if self.revision != actual:
            raise ProjectRevisionError(
                f"revision sidecar declares {self.revision}, but its components aggregate to {actual}."
            )
        _reject_runtime_values(self.to_json())

    def component(self, name: str) -> ComponentEntries | None:
        return next((component for component in self.components if component.name == name), None)


def revision_prefix(revision: str) -> str:
    digest = _revision_digest(revision)
    return f"{REVISION_PREFIX}/sha256/{digest}"


def sidecar_key(revision: str) -> str:
    return f"{revision_prefix(revision)}/{SIDECAR_NAME}"


def _revision_digest(revision: str) -> str:
    match = re.fullmatch(r"sha256:([0-9a-f]{64})", revision)
    if not match:
        raise ProjectRevisionError(f"invalid revision {revision!r}; expected sha256:<64 lowercase hex characters>.")
    return match.group(1)


def _aggregate_components(components: Iterable[ComponentEntries]) -> str:
    flattened = {
        f"{component.name}/{key}": digest for component in components for key, digest in component.entries.items()
    }
    if not flattened:
        raise ProjectRevisionError("cannot compute a revision for an empty project.")
    return floe_revision.aggregate_revision(flattened)


def manifest_schema_errors(payload: dict[str, Any], *, schema_root: Path) -> list[str]:
    """Validate a rendered manifest against its versioned JSON Schema.

    Mirrors `olf.contracts_check.descriptor_schema_errors`/
    `profile_schema_errors`: the hand-rolled model above and this schema
    validate independently, and neither substitutes for the other.
    """
    import jsonschema

    schema_path = schema_root / "project-revision.schema.json"
    schema = json.loads(schema_path.read_text(encoding="utf-8"))
    validator = jsonschema.Draft202012Validator(schema)
    errors = sorted(validator.iter_errors(payload), key=lambda error: list(error.absolute_path))
    return [
        f"schema violation at {'/'.join(str(part) for part in error.absolute_path) or '<root>'}: {error.message}"
        for error in errors
    ]


def _reject_runtime_values(rendered_json: str) -> None:
    for pattern in _FORBIDDEN_VALUE_PATTERNS:
        match = pattern.search(rendered_json)
        if match:
            raise ProjectRevisionError(
                f"project revision manifest contains a stage-bound value ({match.group(0)!r}); "
                "target-stage endpoints and credentials must never enter a promotable revision."
            )


def _file_digest(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _walk_files(root: Path) -> list[Path]:
    if not root.is_dir():
        return []
    return sorted(
        path
        for path in root.rglob("*")
        if path.is_file() and not any(part in _EXCLUDED_DIR_NAMES for part in path.relative_to(root).parts)
    )


def _relative_key(project: ProjectSpec, path: Path) -> str:
    return path.relative_to(project.root).as_posix()


def _descriptors_component(project: ProjectSpec, inventory: Any) -> ComponentEntries:
    paths = [project.lakehouse_path]
    for source in inventory.sources:
        paths.append(project.bronze_root / source.name / "source.yaml")
    entries = {_relative_key(project, path): _file_digest(path) for path in paths}
    return ComponentEntries("descriptors", entries)


def _floe_component(project: ProjectSpec) -> ComponentEntries:
    from olf.deployment.floe_manifests import discover_floe_configs

    configs = discover_floe_configs(project.root)
    entries = {_relative_key(project, path): _file_digest(path) for path in configs}
    return ComponentEntries("floe", entries)


def _dbt_component(project: ProjectSpec, inventory: Any) -> ComponentEntries:
    entries: dict[str, str] = {}
    for product in inventory.products:
        for path in _walk_files(project.gold_root / product.id / "dbt"):
            entries[_relative_key(project, path)] = _file_digest(path)
    return ComponentEntries("dbt", entries)


def _dagster_component(project: ProjectSpec, inventory: Any) -> ComponentEntries:
    paths: list[Path] = []
    for product in inventory.products:
        paths.append(project.pipelines_root / f"{product.id}.py")
    for source in inventory.sources:
        paths.append(project.bronze_root / source.name / "dlt" / f"{source.name}.py")
    entries = {_relative_key(project, path): _file_digest(path) for path in paths if path.is_file()}
    return ComponentEntries("dagster", entries)


def _reports_component(project: ProjectSpec, inventory: Any) -> ComponentEntries | None:
    if not inventory.dashboards:
        return None
    entries: dict[str, str] = {}
    for dashboard in inventory.dashboards:
        for path in _walk_files(project.root / dashboard.report_source_dir):
            entries[_relative_key(project, path)] = _file_digest(path)
    return ComponentEntries("reports", entries)


def is_digest_pinned(image: str) -> bool:
    return bool(_DIGEST_TAG_PATTERN.search(image))


def _default_resolve_image_digest(image: str) -> str:
    from olf.deployment.engine import Toolkit

    tools = Toolkit.default()
    result = tools.docker.image_inspect(image, check=False)
    if not result.ok:
        raise ProjectRevisionError(
            f"cannot inspect image {image!r} (not present locally); "
            "pull/build it first, or pass a digest-pinned reference."
        )
    try:
        payload = json.loads(result.stdout)
        entry = payload[0]
    except (json.JSONDecodeError, IndexError, TypeError) as exc:
        raise ProjectRevisionError(f"cannot inspect image {image!r}: {exc}") from exc
    for repo_digest in entry.get("RepoDigests") or ():
        if is_digest_pinned(repo_digest):
            return repo_digest
    image_id = entry.get("Id")
    if not isinstance(image_id, str) or not image_id.startswith("sha256:"):
        raise ProjectRevisionError(f"image {image!r} has no resolvable digest; push it or build with a tagged base.")
    repository = image.split("@", 1)[0].rsplit(":", 1)[0]
    return f"{repository}@{image_id}"


def resolve_image_digest(image: str, *, resolver: Callable[[str], str] | None = None) -> str:
    """Return a digest-pinned image reference, resolving a local/registry tag if needed."""
    if is_digest_pinned(image):
        return image
    resolve = resolver or _default_resolve_image_digest
    resolved = resolve(image)
    if not is_digest_pinned(resolved):
        raise ProjectRevisionError(f"image {image!r} did not resolve to a digest-pinned reference: {resolved!r}")
    return resolved


def build_project_revision(
    project: ProjectSpec,
    *,
    image: str,
    distribution_version: str,
    resolve_image_digest_with: Callable[[str], str] | None = None,
) -> ProjectRevisionManifest:
    """Compute the frozen, content-addressed revision for one writable project.

    Refuses to build from an invalid project: a revision that omits a
    declared asset, or that freezes a broken descriptor, is worse than no
    revision at all.
    """
    from openlakeforge_domain import load_lakehouse_inventory

    report = validate_project(project)
    if not report.ok:
        failures = "; ".join(f"{check.name}: {check.detail}" for check in report.checks if not check.ok)
        raise ProjectRevisionError(f"project at {project.root} is not valid: {failures}")

    inventory = load_lakehouse_inventory(project.root)
    pinned_image = resolve_image_digest(image, resolver=resolve_image_digest_with)

    components = [
        _descriptors_component(project, inventory),
        _floe_component(project),
        _dbt_component(project, inventory),
        _dagster_component(project, inventory),
    ]
    reports = _reports_component(project, inventory)
    if reports is not None:
        components.append(reports)
    components.append(ComponentEntries("image", {"project-code": pinned_image}))
    components.append(ComponentEntries("distribution", {"version": distribution_version}))

    manifest = ProjectRevisionManifest(
        project_name=inventory.name,
        distribution_version=distribution_version,
        project_code_image=pinned_image,
        components=tuple(components),
        revision=_aggregate_components(components),
    )
    _reject_runtime_values(manifest.to_json())
    schema_root = project.schema_root
    if schema_root.is_dir():
        schema_errors = manifest_schema_errors(json.loads(manifest.to_json()), schema_root=schema_root)
        if schema_errors:
            raise ProjectRevisionError(f"built manifest violates its schema: {'; '.join(schema_errors)}")
    return manifest


def publish(store: RevisionStore, manifest: ProjectRevisionManifest, project: ProjectSpec) -> ProjectRevisionManifest:
    """Publish every entry a manifest declares under its immutable revision prefix.

    Publishes file content, not just digests, so `inspect`/`verify` can run
    against the published revision without a source checkout.
    """
    from olf.artifact_store import ArtifactStoreError

    prefix = revision_prefix(manifest.revision)
    try:
        for component in manifest.components:
            if component.name in {"image", "distribution"}:
                continue
            for relative_key in component.entries:
                path = project.root / relative_key
                content = path.read_bytes()
                publish_immutable(
                    store, f"{prefix}/{component.name}/{relative_key}", content, content_type=_content_type(path)
                )
        publish_immutable(
            store, sidecar_key(manifest.revision), manifest.to_json().encode("utf-8"), content_type="application/json"
        )
    except ArtifactStoreError as exc:
        raise ProjectRevisionError(str(exc)) from exc
    return manifest


def inspect(store: RevisionStore, revision: str) -> ProjectRevisionManifest:
    """Read a published manifest without verifying every referenced object."""
    from olf.artifact_store import ArtifactStoreError

    try:
        content = read_required(store, sidecar_key(revision))
    except ArtifactStoreError as exc:
        raise ProjectRevisionError(str(exc)) from exc
    manifest = ProjectRevisionManifest.from_json(content.decode("utf-8"))
    if manifest.revision != revision:
        raise ProjectRevisionError(
            f"sidecar at {sidecar_key(revision)} declares {manifest.revision}, not requested {revision}."
        )
    return manifest


def verify(
    store: RevisionStore, revision: str, *, running_distribution_version: str | None = None
) -> ProjectRevisionManifest:
    """Verify manifest self-consistency, the compatibility gate, and every published object's digest."""
    from olf.artifact_store import ArtifactStoreError

    manifest = inspect(store, revision)
    if running_distribution_version is not None and manifest.distribution_version != running_distribution_version:
        raise ProjectRevisionError(
            f"revision {revision} was built against distribution {manifest.distribution_version!r}, "
            f"incompatible with the running distribution {running_distribution_version!r}."
        )
    prefix = revision_prefix(revision)
    for component in manifest.components:
        if component.name in {"image", "distribution"}:
            continue
        for relative_key, expected_digest in component.entries.items():
            key = f"{prefix}/{component.name}/{relative_key}"
            try:
                actual_digest = hashlib.sha256(read_required(store, key)).hexdigest()
            except ArtifactStoreError as exc:
                raise ProjectRevisionError(str(exc)) from exc
            if actual_digest != expected_digest:
                raise ProjectRevisionError(
                    f"artifact {relative_key} in component {component.name} of revision {revision} "
                    f"hashes to {actual_digest}, expected {expected_digest}."
                )
    return manifest


def _content_type(path: Path) -> str:
    if path.suffix in {".yml", ".yaml"}:
        return "application/x-yaml"
    if path.suffix == ".json":
        return "application/json"
    if path.suffix == ".py":
        return "text/x-python"
    return "application/octet-stream"
