from __future__ import annotations

import os
from pathlib import Path

from dagster import (
    AssetKey,
    AssetOut,
    AssetSelection,
    Definitions,
    MetadataValue,
    Output,
    define_asset_job,
    multi_asset,
)
from floe_dagster.assets import load_floe_assets
from floe_dagster.definitions import build_runner_from_env

from domains.sales.extract.dlt.sales_poc import SALES_POC_ENTITIES, load_all_entities_to_bronze

_DAGSTER_DIR = Path(__file__).resolve().parent
_SALES_DIR = _DAGSTER_DIR.parents[1]
_FLOE_MANIFEST = _SALES_DIR / "contracts" / "floe" / "manifests" / "sales.manifest.json"
_FLOE_ASSET_PREFIX = "sales"
_REMOTE_MANIFEST_ENV = "OPENLAKEFORGE_FLOE_MANIFEST_URI"
_SALES_JOB_CONFIG = {
    "execution": {
        "config": {
            "multiprocess": {
                "max_concurrent": 1,
            },
        },
    },
}


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
            assets=[sales_poc_bronze_sources],
            jobs=[sales_bronze_to_silver_job],
        ),
        floe_defs,
    )


defs = _build_defs()
