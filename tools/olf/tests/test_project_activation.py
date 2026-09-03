from __future__ import annotations

import json
from pathlib import Path

import pytest

from olf import project_activation, project_revision
from olf.artifact_store import FilesystemRevisionStore
from olf.project import ProjectSpec

_IMAGE = "ghcr.io/openlakeforge/project-code@sha256:" + "a" * 64
_DIGEST = "sha256:" + "b" * 64


def _activation() -> project_activation.ProjectActivation:
    return project_activation.ProjectActivation(
        deployment_profile="example",
        provider="local",
        stage="dev",
        project_name="widgets",
        project_revision=_DIGEST,
        distribution_version="0.3.0-alpha.1",
        project_code_image=_IMAGE,
        floe_manifest_revision="sha256:" + "c" * 64,
        provider_binding_digest="sha256:" + "d" * 64,
        capabilities={"analytics": True, "governance": False},
    )


def test_activation_hash_is_deterministic_and_round_trips() -> None:
    first = _activation().resolved()
    second = _activation().resolved()

    assert first.activation_revision == second.activation_revision
    assert project_activation.ProjectActivation.from_json(first.to_json()) == first


def test_activation_rejects_unknown_fields() -> None:
    payload = json.loads(_activation().to_json())
    payload["unexpected"] = True

    with pytest.raises(project_activation.ProjectActivationError, match="field set"):
        project_activation.ProjectActivation.from_json(json.dumps(payload))


def test_active_pointer_reads_immutable_activation(tmp_path: Path) -> None:
    store = FilesystemRevisionStore(tmp_path / "store")
    activation = project_activation.publish(store, _activation())
    project_activation.commit_active(store, activation)

    assert project_activation.active(store, stage="dev") == activation
    assert project_activation.active(store, stage="prod") is None


def test_materialize_uses_only_verified_published_content(external_project: Path, tmp_path: Path) -> None:
    distribution_root = Path(__file__).resolve().parents[3]
    spec = ProjectSpec(root=external_project, distribution_root=distribution_root)
    manifest = project_revision.build_project_revision(
        spec,
        image=_IMAGE,
        distribution_version="0.2.0-alpha.1",
    )
    store = FilesystemRevisionStore(tmp_path / "store")
    project_revision.publish(store, manifest, spec)

    source = external_project / "lakehouse_code" / "lakehouse.yaml"
    source.write_text("mutated after publish\n")
    restored = project_revision.materialize(store, manifest, tmp_path / "materialized")

    assert (restored / "lakehouse_code" / "lakehouse.yaml").read_text() != source.read_text()


def test_materialize_rejects_path_escape(tmp_path: Path) -> None:
    store = FilesystemRevisionStore(tmp_path / "store")
    component = project_revision.ComponentEntries("descriptors", {"../escape": "0" * 64})
    image = project_revision.ComponentEntries("image", {"project-code": _IMAGE})
    distribution = project_revision.ComponentEntries("distribution", {"version": "0.2.0-alpha.1"})
    required = [
        component,
        project_revision.ComponentEntries("floe", {}),
        project_revision.ComponentEntries("dbt", {}),
        project_revision.ComponentEntries("dagster", {}),
        image,
        distribution,
    ]
    manifest = project_revision.ProjectRevisionManifest(
        project_name="widgets",
        distribution_version="0.2.0-alpha.1",
        project_code_image=_IMAGE,
        components=tuple(required),
        revision=project_revision._aggregate_components(required),  # noqa: SLF001 - crafted hostile sidecar shape
    )

    with pytest.raises(project_revision.ProjectRevisionError, match="unsafe artifact path"):
        project_revision.materialize(store, manifest, tmp_path / "materialized")
