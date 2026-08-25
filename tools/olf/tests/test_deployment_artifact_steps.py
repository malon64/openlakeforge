from __future__ import annotations

from contextlib import contextmanager
from pathlib import Path
from typing import Any

import pytest
import typer

from olf.deployment import artifact_steps
from olf.deployment.errors import DeploymentPreconditionError


def test_artifact_operation_error_is_a_deployment_precondition_error() -> None:
    assert issubclass(artifact_steps.ArtifactOperationError, DeploymentPreconditionError)


def test_sync_catalog_namespaces_wraps_typer_exit(monkeypatch: pytest.MonkeyPatch) -> None:
    def _raises_exit(*, dry_run: bool, prune: Any) -> None:  # noqa: ARG001
        raise typer.Exit(code=1)

    monkeypatch.setattr("olf.commands.catalog.catalog_sync_namespaces", _raises_exit)

    with pytest.raises(artifact_steps.ArtifactOperationError):
        artifact_steps.sync_catalog_namespaces()


def test_activate_runtime_revision_requires_discovered_uploads(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    monkeypatch.setattr(artifact_steps.s3, "discover_runtime_artifacts", lambda runtime_root: [])

    with pytest.raises(artifact_steps.ArtifactOperationError, match="no rendered Floe runtime artifacts"):
        artifact_steps.activate_runtime_revision(tmp_path)


def test_activate_runtime_revision_passes_via_through_to_storage_client(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    monkeypatch.setattr(artifact_steps.s3, "discover_runtime_artifacts", lambda runtime_root: ["upload"])
    monkeypatch.setattr(artifact_steps.olf_config, "env", lambda name, default="": "ops-bucket")

    seen_via: list[str] = []

    @contextmanager
    def _fake_client(via: str, bucket: str):  # noqa: ANN202
        seen_via.append(via)
        assert bucket == "ops-bucket"
        yield object()

    monkeypatch.setattr("olf.commands.revision._artifact_storage_client", _fake_client)

    class _Manifest:
        revision = "sha256:abc"

    monkeypatch.setattr(artifact_steps.revision, "activate", lambda client, bucket, uploads: _Manifest())

    result = artifact_steps.activate_runtime_revision(tmp_path, via="direct")

    assert result == "sha256:abc"
    assert seen_via == ["direct"]


def test_activate_runtime_revision_defaults_to_port_forward(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    monkeypatch.setattr(artifact_steps.s3, "discover_runtime_artifacts", lambda runtime_root: ["upload"])
    monkeypatch.setattr(artifact_steps.olf_config, "env", lambda name, default="": "ops-bucket")

    seen_via: list[str] = []

    @contextmanager
    def _fake_client(via: str, bucket: str):  # noqa: ANN202
        seen_via.append(via)
        yield object()

    monkeypatch.setattr("olf.commands.revision._artifact_storage_client", _fake_client)

    class _Manifest:
        revision = "sha256:abc"

    monkeypatch.setattr(artifact_steps.revision, "activate", lambda client, bucket, uploads: _Manifest())

    artifact_steps.activate_runtime_revision(tmp_path)

    assert seen_via == ["port-forward"]


def test_activate_runtime_revision_wraps_revision_error(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    monkeypatch.setattr(artifact_steps.s3, "discover_runtime_artifacts", lambda runtime_root: ["upload"])
    monkeypatch.setattr(artifact_steps.olf_config, "env", lambda name, default="": "ops-bucket")

    @contextmanager
    def _fake_client(via: str, bucket: str):  # noqa: ANN202, ARG001
        yield object()

    monkeypatch.setattr("olf.commands.revision._artifact_storage_client", _fake_client)

    def _raise(client: Any, bucket: str, uploads: Any) -> None:
        raise artifact_steps.revision.RevisionError("digest mismatch")

    monkeypatch.setattr(artifact_steps.revision, "activate", _raise)

    with pytest.raises(artifact_steps.ArtifactOperationError, match="digest mismatch"):
        artifact_steps.activate_runtime_revision(tmp_path)


def test_upload_runtime_manifests_passes_via_through(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    calls: list[dict[str, Any]] = []

    def _fake_upload(*, via: str, manifest_root: str, runtime_root: str) -> None:
        calls.append({"via": via, "manifest_root": manifest_root, "runtime_root": runtime_root})

    monkeypatch.setattr("olf.commands.artifacts.upload_manifests", _fake_upload)

    artifact_steps.upload_runtime_manifests(tmp_path, via="direct")

    assert calls == [{"via": "direct", "manifest_root": "", "runtime_root": str(tmp_path)}]


def test_upload_runtime_manifests_wraps_typer_exit(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    def _raises_exit(*, via: str, manifest_root: str, runtime_root: str) -> None:  # noqa: ARG001
        raise typer.Exit(code=1)

    monkeypatch.setattr("olf.commands.artifacts.upload_manifests", _raises_exit)

    with pytest.raises(artifact_steps.ArtifactOperationError):
        artifact_steps.upload_runtime_manifests(tmp_path)


def test_deploy_optional_layer_artifacts_delegates_to_layers_module(monkeypatch: pytest.MonkeyPatch) -> None:
    calls: list[str] = []
    monkeypatch.setattr(
        artifact_steps.layers,
        "deploy_enabled_artifacts",
        lambda environ, *, deploy_reports, deploy_metadata, report: calls.append("deploy_enabled_artifacts"),
    )
    monkeypatch.setattr("olf.commands.superset.deploy_superset_reports", lambda *a, **k: None)
    monkeypatch.setattr("olf.commands.openmetadata.deploy_openmetadata_metadata", lambda *a, **k: None)

    artifact_steps.deploy_optional_layer_artifacts({})

    assert calls == ["deploy_enabled_artifacts"]
