from __future__ import annotations

from pathlib import Path

import pytest

from olf import project_revision
from olf.artifact_store import FilesystemRevisionStore
from olf.project import ProjectSpec

_DISTRIBUTION_ROOT = Path(__file__).resolve().parents[3]
_DIGEST_IMAGE = "ghcr.io/malon64/openlakeforge-project-code@sha256:" + "a" * 64
_DISTRIBUTION_VERSION = "0.2.0-alpha.1"


def _spec(root: Path) -> ProjectSpec:
    return ProjectSpec(root=root, distribution_root=_DISTRIBUTION_ROOT)


def _build(root: Path) -> project_revision.ProjectRevisionManifest:
    return project_revision.build_project_revision(
        _spec(root), image=_DIGEST_IMAGE, distribution_version=_DISTRIBUTION_VERSION
    )


def test_revision_is_stable_across_repeated_builds(external_project: Path) -> None:
    first = _build(external_project)
    second = _build(external_project)

    assert first.revision == second.revision
    assert first.to_json() == second.to_json()


def test_revision_is_stable_across_a_copy_at_a_different_path(external_project: Path, tmp_path: Path) -> None:
    import shutil

    copy_root = tmp_path / "copy-of-project"
    shutil.copytree(external_project, copy_root)

    assert _build(external_project).revision == _build(copy_root).revision


def test_editing_the_lakehouse_descriptor_changes_the_revision(external_project: Path) -> None:
    before = _build(external_project)

    descriptor = external_project / "lakehouse_code/lakehouse.yaml"
    descriptor.write_text(descriptor.read_text() + "\n# a harmless trailing comment\n")

    after = _build(external_project)

    assert before.revision != after.revision


def test_editing_the_deployment_profile_does_not_change_the_revision(external_project: Path) -> None:
    before = _build(external_project)

    profile = external_project / "openlakeforge.yaml"
    profile.write_text(profile.read_text() + "\n# deployment intent is not project content\n")

    after = _build(external_project)

    assert before.revision == after.revision


def test_build_refuses_an_invalid_project(external_project: Path) -> None:
    (external_project / "lakehouse_code" / "lakehouse.yaml").unlink()

    with pytest.raises(project_revision.ProjectRevisionError, match="is not valid"):
        _build(external_project)


def test_build_rejects_a_bare_mutable_image_tag(external_project: Path) -> None:
    with pytest.raises(project_revision.ProjectRevisionError):
        project_revision.build_project_revision(
            _spec(external_project),
            image="ghcr.io/malon64/openlakeforge-project-code:local",
            distribution_version=_DISTRIBUTION_VERSION,
        )


def test_build_resolves_a_mutable_tag_through_the_injected_resolver(external_project: Path) -> None:
    resolved = "ghcr.io/malon64/openlakeforge-project-code@sha256:" + "b" * 64
    manifest = project_revision.build_project_revision(
        _spec(external_project),
        image="ghcr.io/malon64/openlakeforge-project-code:local",
        distribution_version=_DISTRIBUTION_VERSION,
        resolve_image_digest_with=lambda image: resolved,  # noqa: ARG005
    )

    assert manifest.project_code_image == resolved


def test_manifest_contains_no_stage_bound_values(external_project: Path) -> None:
    manifest = _build(external_project)

    rendered = manifest.to_json()
    for forbidden in ("s3://", "http://", "https://"):
        assert forbidden not in rendered


def test_manifest_json_round_trips(external_project: Path) -> None:
    manifest = _build(external_project)

    restored = project_revision.ProjectRevisionManifest.from_json(manifest.to_json())

    assert restored == manifest


def test_publish_and_verify_a_revision(external_project: Path, tmp_path: Path) -> None:
    manifest = _build(external_project)
    store = FilesystemRevisionStore(tmp_path / "store")

    project_revision.publish(store, manifest, _spec(external_project))
    verified = project_revision.verify(store, manifest.revision, running_distribution_version=_DISTRIBUTION_VERSION)

    assert verified == manifest


def test_publish_is_idempotent(external_project: Path, tmp_path: Path) -> None:
    manifest = _build(external_project)
    store = FilesystemRevisionStore(tmp_path / "store")

    project_revision.publish(store, manifest, _spec(external_project))
    project_revision.publish(store, manifest, _spec(external_project))  # no error on repeat

    assert project_revision.inspect(store, manifest.revision) == manifest


def test_verify_detects_a_tampered_object(external_project: Path, tmp_path: Path) -> None:
    manifest = _build(external_project)
    store = FilesystemRevisionStore(tmp_path / "store")
    project_revision.publish(store, manifest, _spec(external_project))

    descriptors = manifest.component("descriptors")
    assert descriptors is not None
    tampered_key = next(iter(descriptors.entries))
    prefix = project_revision.revision_prefix(manifest.revision)
    (tmp_path / "store" / f"{prefix}/descriptors/{tampered_key}").write_bytes(b"tampered")

    with pytest.raises(project_revision.ProjectRevisionError, match="hashes to"):
        project_revision.verify(store, manifest.revision)


def test_verify_detects_partial_publication(external_project: Path, tmp_path: Path) -> None:
    manifest = _build(external_project)
    store = FilesystemRevisionStore(tmp_path / "store")
    project_revision.publish(store, manifest, _spec(external_project))

    descriptors = manifest.component("descriptors")
    assert descriptors is not None
    missing_key = next(iter(descriptors.entries))
    prefix = project_revision.revision_prefix(manifest.revision)
    (tmp_path / "store" / f"{prefix}/descriptors/{missing_key}").unlink()

    with pytest.raises(project_revision.ProjectRevisionError, match="missing"):
        project_revision.verify(store, manifest.revision)


def test_verify_fails_closed_on_an_incompatible_distribution(external_project: Path, tmp_path: Path) -> None:
    manifest = _build(external_project)
    store = FilesystemRevisionStore(tmp_path / "store")
    project_revision.publish(store, manifest, _spec(external_project))

    with pytest.raises(project_revision.ProjectRevisionError, match="incompatible"):
        project_revision.verify(store, manifest.revision, running_distribution_version="9.9.9-alpha.1")


def test_inspect_rejects_a_sidecar_declaring_a_different_revision(external_project: Path, tmp_path: Path) -> None:
    manifest = _build(external_project)
    store = FilesystemRevisionStore(tmp_path / "store")
    other_revision = "sha256:" + ("1" if manifest.revision[-1] != "1" else "2") * 64
    store.write(
        project_revision.sidecar_key(other_revision),
        manifest.to_json().encode("utf-8"),
        content_type="application/json",
    )

    with pytest.raises(project_revision.ProjectRevisionError, match="not requested"):
        project_revision.inspect(store, other_revision)


def test_validate_rejects_a_top_level_image_field_that_drifted_from_its_component(
    external_project: Path,
) -> None:
    manifest = _build(external_project)
    tampered = project_revision.ProjectRevisionManifest(
        project_name=manifest.project_name,
        distribution_version=manifest.distribution_version,
        project_code_image="ghcr.io/malon64/openlakeforge-project-code@sha256:" + "9" * 64,
        components=manifest.components,
        revision=manifest.revision,
    )

    with pytest.raises(project_revision.ProjectRevisionError, match="does not match the manifest's image"):
        project_revision.ProjectRevisionManifest.from_json(tampered.to_json())


def test_validate_rejects_a_top_level_distribution_field_that_drifted_from_its_component(
    external_project: Path,
) -> None:
    manifest = _build(external_project)
    tampered = project_revision.ProjectRevisionManifest(
        project_name=manifest.project_name,
        distribution_version="9.9.9-alpha.1",
        project_code_image=manifest.project_code_image,
        components=manifest.components,
        revision=manifest.revision,
    )

    with pytest.raises(project_revision.ProjectRevisionError, match="does not match the manifest's distribution"):
        project_revision.ProjectRevisionManifest.from_json(tampered.to_json())


def test_build_rejects_a_component_file_containing_a_stage_bound_value(external_project: Path) -> None:
    descriptor = external_project / "lakehouse_code/lakehouse.yaml"
    descriptor.write_text(descriptor.read_text() + "\n# s3://leaked-ops-bucket/floe/reports\n")

    with pytest.raises(project_revision.ProjectRevisionError, match="stage-bound value"):
        _build(external_project)


def test_publish_fails_closed_when_a_source_file_changes_after_build(
    external_project: Path, tmp_path: Path
) -> None:
    manifest = _build(external_project)
    store = FilesystemRevisionStore(tmp_path / "store")

    descriptor = external_project / "lakehouse_code/lakehouse.yaml"
    descriptor.write_text(descriptor.read_text() + "\n# drifted after build, before publish\n")

    with pytest.raises(project_revision.ProjectRevisionError, match="changed on disk since the revision was built"):
        project_revision.publish(store, manifest, _spec(external_project))

    # Nothing under this revision's prefix should be readable -- publish must
    # not have written the drifted bytes under a sidecar that still claims
    # the original digest.
    assert store.read(project_revision.sidecar_key(manifest.revision)) is None


def test_resolve_image_digest_rejects_a_local_config_id_without_a_repo_digest(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from olf.deployment import engine as engine_module
    from olf.tooling.process import CommandResult

    class _FakeDocker:
        def image_inspect(self, image: str, *, check: bool = False) -> CommandResult:  # noqa: ARG002
            import json as _json

            payload = _json.dumps([{"Id": "sha256:" + "0" * 64, "RepoDigests": []}])
            return CommandResult(argv=("docker",), returncode=0, stdout=payload, stderr="", duration_seconds=0.0)

    class _FakeToolkit:
        docker = _FakeDocker()

    monkeypatch.setattr(engine_module.Toolkit, "default", classmethod(lambda cls: _FakeToolkit()))

    with pytest.raises(project_revision.ProjectRevisionError, match="no registry digest"):
        project_revision.resolve_image_digest("ghcr.io/malon64/openlakeforge-project-code:local")
