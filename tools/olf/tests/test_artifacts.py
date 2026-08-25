from pathlib import Path

import pytest

from olf import auth, s3


def test_manifest_key_layout() -> None:
    assert s3.manifest_key("sales") == "floe/manifests/sales/sales.manifest.json"


def test_discover_tracked_manifests(tmp_path: Path) -> None:
    manifest = tmp_path / "lakehouse_code/silver/sales/contracts/floe/manifests/sales.manifest.json"
    manifest.parent.mkdir(parents=True)
    manifest.write_text("{}")
    uploads = s3.discover_tracked_manifests(tmp_path)
    assert len(uploads) == 1
    assert uploads[0].path == manifest
    assert uploads[0].key == "floe/manifests/sales/sales.manifest.json"


def test_discover_runtime_manifests(tmp_path: Path) -> None:
    manifest = tmp_path / "supply_chain/supply_chain.manifest.json"
    manifest.parent.mkdir(parents=True)
    manifest.write_text("{}")
    uploads = s3.discover_runtime_manifests(tmp_path)
    assert len(uploads) == 1
    assert uploads[0].path == manifest
    assert uploads[0].key == "floe/manifests/supply_chain/supply_chain.manifest.json"


def test_discover_runtime_artifacts(tmp_path: Path) -> None:
    config = tmp_path / "configs/sales/sales.yml"
    profile = tmp_path / "profiles/sales/local-k8s.yml"
    manifest = tmp_path / "manifests/sales/sales.manifest.json"
    for path in [config, profile, manifest]:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text("{}")

    uploads = s3.discover_runtime_artifacts(tmp_path)
    assert [(upload.path, upload.key) for upload in uploads] == [
        (config, "floe/configs/sales/sales.yml"),
        (profile, "floe/profiles/sales/local-k8s.yml"),
        (manifest, "floe/manifests/sales/sales.manifest.json"),
    ]


def test_upload_direct_uses_the_selected_olf_aws_session(monkeypatch: pytest.MonkeyPatch) -> None:
    calls: list[object] = []
    sdk_client = object()

    class Session:
        def client(self, service: str, **kwargs):  # noqa: ANN003, ANN201
            calls.append((service, kwargs))
            return sdk_client

    monkeypatch.setattr(auth, "aws_session", lambda environ, *, region: calls.append((environ, region)) or Session())
    monkeypatch.setattr(s3, "_put_objects", lambda client, bucket, uploads: calls.append((client, bucket, uploads)))

    s3.upload_direct("ops", [], region="eu-west-1")

    assert calls[0][1] == "eu-west-1"
    assert calls[1] == ("s3", {"region_name": "eu-west-1"})
    assert calls[2] == (sdk_client, "ops", [])
