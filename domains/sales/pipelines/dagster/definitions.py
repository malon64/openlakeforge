from __future__ import annotations

import os
import subprocess
from pathlib import Path
from urllib.parse import urlparse

from dagster import (
    AssetKey,
    AssetOut,
    AssetSelection,
    Definitions,
    MetadataValue,
    Output,
    asset,
    define_asset_job,
    multi_asset,
)
from dagster_dbt import DagsterDbtTranslator, DbtCliResource, dbt_assets
from floe_dagster.assets import load_floe_assets
from floe_dagster.definitions import build_runner_from_env
from trino.dbapi import connect

from domains.sales.extract.dlt.sales_poc import SALES_POC_ENTITIES, load_all_entities_to_bronze

_DAGSTER_DIR = Path(__file__).resolve().parent
_SALES_DIR = _DAGSTER_DIR.parents[1]
_FLOE_MANIFEST = _SALES_DIR / "contracts" / "floe" / "manifests" / "sales.manifest.json"
_DBT_PROJECT_DIR = _SALES_DIR / "transformations" / "dbt"
_DBT_MANIFEST = _DBT_PROJECT_DIR / "target" / "manifest.json"
_FLOE_ASSET_PREFIX = "sales"
_DBT_GOLD_ASSETS = (
    "mart_sales_by_day",
    "mart_revenue_by_product",
    "mart_sales_by_customer",
)
_REMOTE_MANIFEST_ENV = "OPENLAKEFORGE_FLOE_MANIFEST_URI"
_TRINO_URI_ENV = "OPENLAKEFORGE_TRINO_URI"
_SALES_JOB_CONFIG = {
    "execution": {
        "config": {
            "multiprocess": {
                "max_concurrent": 1,
            },
        },
    },
}


class SalesDbtTranslator(DagsterDbtTranslator):
    def get_asset_key(self, dbt_resource_props) -> AssetKey:
        if dbt_resource_props["resource_type"] == "source":
            return AssetKey([_FLOE_ASSET_PREFIX, dbt_resource_props["name"]])
        return AssetKey([_FLOE_ASSET_PREFIX, dbt_resource_props["name"]])

    def get_group_name(self, dbt_resource_props) -> str:
        return "sales"


@multi_asset(
    name="sales_poc_bronze_sources",
    outs={
        f"{entity}_source": AssetOut(
            key=AssetKey([_FLOE_ASSET_PREFIX, f"{entity}_source"]),
            is_required=True,
        )
        for entity in SALES_POC_ENTITIES
    },
    group_name="sales",
)
def sales_poc_bronze_sources(context):
    results = load_all_entities_to_bronze()
    for entity, result in results.items():
        context.log.info("Loaded %s Bronze source to %s", entity, result.uri)
        yield Output(
            value=None,
            output_name=f"{entity}_source",
            metadata={
                "rows": MetadataValue.int(result.rows),
                "bronze_uri": MetadataValue.url(result.uri),
                "source_file": MetadataValue.path(result.source_file),
            },
        )


def _ensure_dbt_manifest() -> Path:
    if _DBT_MANIFEST.exists():
        return _DBT_MANIFEST

    env = os.environ.copy()
    env.setdefault("AWS_ACCESS_KEY_ID", "openlakeforge")
    env.setdefault("AWS_SECRET_ACCESS_KEY", "openlakeforge")
    env.setdefault("AWS_REGION", "us-east-1")
    env.setdefault("AWS_DEFAULT_REGION", env["AWS_REGION"])
    env.setdefault("AWS_ENDPOINT_URL_S3", "http://seaweedfs-s3:8333")
    env.setdefault("POLARIS_DBT_CLIENT_ID", "openlakeforge-dbt")
    env.setdefault("POLARIS_DBT_CLIENT_SECRET", "openlakeforge-dbt")
    env.setdefault("POLARIS_REST_URI", "http://polaris:8181/api/catalog")
    env.setdefault("POLARIS_TOKEN_URI", "http://polaris:8181/api/catalog/v1/oauth/tokens")
    env.setdefault("POLARIS_WAREHOUSE", "lakehouse")
    env.setdefault("POLARIS_OAUTH_SCOPE", "PRINCIPAL_ROLE:ALL")

    subprocess.run(
        [
            "dbt",
            "parse",
            "--project-dir",
            str(_DBT_PROJECT_DIR),
            "--profiles-dir",
            str(_DBT_PROJECT_DIR),
        ],
        check=True,
        env=env,
    )
    return _DBT_MANIFEST


@dbt_assets(
    manifest=_ensure_dbt_manifest(),
    dagster_dbt_translator=SalesDbtTranslator(),
)
def sales_dbt_gold_assets(context, dbt: DbtCliResource):
    yield from dbt.cli(["build"], context=context).stream()


@asset(
    key=AssetKey([_FLOE_ASSET_PREFIX, "sales_gold_trino_smoke_test"]),
    group_name="sales",
    deps=[AssetKey([_FLOE_ASSET_PREFIX, asset_name]) for asset_name in _DBT_GOLD_ASSETS],
)
def sales_gold_trino_smoke_test() -> dict[str, int]:
    uri = os.environ.get(_TRINO_URI_ENV, "http://trino:8080")
    parsed = urlparse(uri)
    host = parsed.hostname or "trino"
    port = parsed.port or 8080

    with connect(host=host, port=port, user="openlakeforge") as conn:
        cursor = conn.cursor()
        row_counts: dict[str, int] = {}
        for table_name in _DBT_GOLD_ASSETS:
            cursor.execute(f"select count(*) from iceberg.sales_gold.{table_name}")
            row_counts[table_name] = int(cursor.fetchone()[0])

    if any(count <= 0 for count in row_counts.values()):
        raise RuntimeError(f"Gold Trino smoke test found empty marts: {row_counts}")

    return row_counts


sales_bronze_to_silver_job = define_asset_job(
    name="sales_bronze_to_silver_job",
    selection=(
        AssetSelection.keys(
            *[
                AssetKey([_FLOE_ASSET_PREFIX, f"{entity}_source"])
                for entity in SALES_POC_ENTITIES
            ],
            *[AssetKey([_FLOE_ASSET_PREFIX, entity]) for entity in SALES_POC_ENTITIES],
            *[
                AssetKey([_FLOE_ASSET_PREFIX, f"{entity}_rejected"])
                for entity in SALES_POC_ENTITIES
            ],
        ).required_multi_asset_neighbors()
    ),
    config=_SALES_JOB_CONFIG,
)

sales_bronze_to_gold_job = define_asset_job(
    name="sales_bronze_to_gold_job",
    selection=(
        AssetSelection.keys(
            *[
                AssetKey([_FLOE_ASSET_PREFIX, f"{entity}_source"])
                for entity in SALES_POC_ENTITIES
            ],
            *[AssetKey([_FLOE_ASSET_PREFIX, entity]) for entity in SALES_POC_ENTITIES],
            *[
                AssetKey([_FLOE_ASSET_PREFIX, f"{entity}_rejected"])
                for entity in SALES_POC_ENTITIES
            ],
            *[AssetKey([_FLOE_ASSET_PREFIX, asset_name]) for asset_name in _DBT_GOLD_ASSETS],
            AssetKey([_FLOE_ASSET_PREFIX, "sales_gold_trino_smoke_test"]),
        ).required_multi_asset_neighbors()
    ),
    config=_SALES_JOB_CONFIG,
)


def _manifest_path_for_dagster() -> str:
    remote_uri = os.environ.get(_REMOTE_MANIFEST_ENV)
    if remote_uri:
        return remote_uri

    if not _FLOE_MANIFEST.exists():
        raise RuntimeError(
            f"Missing Floe manifest at {_FLOE_MANIFEST}. "
            "Run 'make floe-manifest' before loading Dagster definitions."
        )
    return str(_FLOE_MANIFEST)


def _build_defs() -> Definitions:
    floe_defs = load_floe_assets(
        manifest_path=_manifest_path_for_dagster(),
        runner=build_runner_from_env(),
        register_source_assets=False,
    )
    return Definitions.merge(
        Definitions(
            assets=[sales_poc_bronze_sources, sales_dbt_gold_assets, sales_gold_trino_smoke_test],
            jobs=[sales_bronze_to_silver_job, sales_bronze_to_gold_job],
            resources={
                "dbt": DbtCliResource(
                    project_dir=str(_DBT_PROJECT_DIR),
                    profiles_dir=str(_DBT_PROJECT_DIR),
                    target="local",
                ),
            },
        ),
        floe_defs,
    )


defs = _build_defs()
