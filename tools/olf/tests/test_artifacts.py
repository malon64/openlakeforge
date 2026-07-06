from pathlib import Path

from olf import s3


def test_manifest_key_layout() -> None:
    assert s3.manifest_key("sales", "order_revenue") == (
        "floe/manifests/sales/order_revenue/order_revenue.manifest.json"
    )


def test_discover_tracked_manifests(tmp_path: Path) -> None:
    manifest = tmp_path / "domains/sales/contracts/floe/manifests/order_revenue.manifest.json"
    manifest.parent.mkdir(parents=True)
    manifest.write_text("{}")
    uploads = s3.discover_tracked_manifests(tmp_path)
    assert len(uploads) == 1
    assert uploads[0].path == manifest
    assert uploads[0].key == "floe/manifests/sales/order_revenue/order_revenue.manifest.json"


def test_discover_runtime_manifests(tmp_path: Path) -> None:
    # floe-manifest.sh persists the two-level <domain>/<product>/ layout.
    manifest = tmp_path / "supply_chain/inventory_reliability/inventory_reliability.manifest.json"
    manifest.parent.mkdir(parents=True)
    manifest.write_text("{}")
    uploads = s3.discover_runtime_manifests(tmp_path)
    assert len(uploads) == 1
    assert uploads[0].path == manifest
    assert uploads[0].key == (
        "floe/manifests/supply_chain/inventory_reliability/inventory_reliability.manifest.json"
    )


def test_discover_runtime_artifacts(tmp_path: Path) -> None:
    config = tmp_path / "configs/sales/order_revenue/order_revenue.yml"
    profile = tmp_path / "profiles/sales/order_revenue/local-k8s.yml"
    manifest = tmp_path / "manifests/sales/order_revenue/order_revenue.manifest.json"
    for path in [config, profile, manifest]:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text("{}")

    uploads = s3.discover_runtime_artifacts(tmp_path)
    assert [(upload.path, upload.key) for upload in uploads] == [
        (config, "floe/configs/sales/order_revenue/order_revenue.yml"),
        (profile, "floe/profiles/sales/order_revenue/local-k8s.yml"),
        (manifest, "floe/manifests/sales/order_revenue/order_revenue.manifest.json"),
    ]
