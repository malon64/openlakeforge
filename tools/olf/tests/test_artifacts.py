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
