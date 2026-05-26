from pathlib import Path

from dagster import AssetKey
from floe_dagster.manifest import load_manifest

from domains.sales.extract.dlt.sales_poc import SALES_POC_ENTITIES
from domains.sales.pipelines.dagster.definitions import defs


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


def test_iteration3_job_and_source_assets_are_registered() -> None:
    assert defs.get_job_def("iteration3_sales_silver_job").name == "iteration3_sales_silver_job"
    asset_keys = {key for asset_def in defs.assets for key in asset_def.keys}
    for entity in SALES_POC_ENTITIES:
        assert AssetKey(["sales", f"{entity}_source"]) in asset_keys
        assert AssetKey(["sales", entity]) in asset_keys
