from pathlib import Path

from dagster import AssetKey
from floe_dagster.manifest import load_manifest

from domains.sales.extract.dlt.sales_poc import SALES_POC_ENTITIES
from domains.sales.pipelines.dagster.definitions import defs

FLOE_ASSET_PREFIX = "default"


def test_sales_floe_manifest_loads() -> None:
    manifest_path = (
        Path(__file__).resolve().parents[1]
        / "contracts"
        / "floe"
        / "manifests"
        / "sales.manifest.json"
    )
    manifest = load_manifest(manifest_path)
    assert [entity.name for entity in manifest.entities] == list(SALES_POC_ENTITIES)


def test_sales_bronze_to_silver_job_and_assets_are_registered() -> None:
    assert defs.resolve_job_def("sales_bronze_to_silver_job").name == "sales_bronze_to_silver_job"
    asset_keys = {key for asset_def in defs.assets for key in asset_def.keys}
    for entity in SALES_POC_ENTITIES:
        assert AssetKey([FLOE_ASSET_PREFIX, f"{entity}_source"]) in asset_keys
        assert AssetKey([FLOE_ASSET_PREFIX, entity]) in asset_keys
