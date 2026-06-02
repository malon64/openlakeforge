from pathlib import Path

from dagster import AssetKey
from floe_dagster.manifest import load_manifest

from domains.sales.extract.dlt.sales_poc import SALES_POC_ENTITIES
from domains.sales.pipelines.dagster.definitions import defs

FLOE_ASSET_PREFIX = "sales"
DBT_GOLD_ASSETS = {
    "mart_sales_by_day",
    "mart_revenue_by_product",
    "mart_sales_by_customer",
}


def test_sales_floe_manifest_loads() -> None:
    manifest_path = (
        Path(__file__).resolve().parents[1]
        / "contracts"
        / "floe"
        / "manifests"
        / "sales.manifest.json"
    )
    manifest = load_manifest(manifest_path)
    assert {entity.name for entity in manifest.entities} == set(SALES_POC_ENTITIES)
    assert manifest.execution.base_args == [
        "run",
        "--manifest",
        "{manifest_uri}",
        "--log-format",
        "json",
        "--quiet",
    ]
    for entity in manifest.entities:
        assert entity.group_name == "sales"
        assert entity.asset_key == ["sales", entity.name]


def test_sales_etl_pipeline_and_assets_are_registered() -> None:
    assert defs.resolve_job_def("sales_etl_pipeline").name == "sales_etl_pipeline"
    asset_keys = {key for asset_def in defs.assets for key in asset_def.keys}
    for entity in SALES_POC_ENTITIES:
        assert AssetKey([FLOE_ASSET_PREFIX, f"{entity}_source"]) in asset_keys
        assert AssetKey([FLOE_ASSET_PREFIX, entity]) in asset_keys
    for asset_name in DBT_GOLD_ASSETS:
        assert AssetKey([FLOE_ASSET_PREFIX, asset_name]) in asset_keys


def test_sales_floe_assets_depend_on_bronze_sources() -> None:
    asset_deps = {
        key: deps
        for asset_def in defs.assets
        for key, deps in asset_def.asset_deps.items()
    }

    for entity in SALES_POC_ENTITIES:
        bronze_key = AssetKey([FLOE_ASSET_PREFIX, f"{entity}_source"])
        assert asset_deps[AssetKey([FLOE_ASSET_PREFIX, entity])] == {bronze_key}
        assert asset_deps[AssetKey([FLOE_ASSET_PREFIX, f"{entity}_rejected"])] == {
            bronze_key
        }


def test_sales_dbt_gold_assets_depend_on_silver_assets() -> None:
    asset_deps = {
        key: deps
        for asset_def in defs.assets
        for key, deps in asset_def.asset_deps.items()
    }

    assert asset_deps[AssetKey([FLOE_ASSET_PREFIX, "mart_sales_by_day"])] == {
        AssetKey([FLOE_ASSET_PREFIX, "sales"])
    }
    assert asset_deps[AssetKey([FLOE_ASSET_PREFIX, "mart_revenue_by_product"])] == {
        AssetKey([FLOE_ASSET_PREFIX, "sales"]),
        AssetKey([FLOE_ASSET_PREFIX, "products"]),
    }
    assert asset_deps[AssetKey([FLOE_ASSET_PREFIX, "mart_sales_by_customer"])] == {
        AssetKey([FLOE_ASSET_PREFIX, "sales"]),
        AssetKey([FLOE_ASSET_PREFIX, "customers"]),
    }
