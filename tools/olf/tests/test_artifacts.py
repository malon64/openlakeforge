from pathlib import Path

from olf import s3


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
