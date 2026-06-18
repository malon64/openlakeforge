from __future__ import annotations

import os
import subprocess
from collections.abc import Callable
from dataclasses import dataclass
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
from dagster_dbt import DagsterDbtTranslator, DbtCliResource, dbt_assets
from floe_dagster.assets import load_floe_assets
from floe_dagster.definitions import build_job_run_config_from_manifest, build_runner_from_env
from floe_dagster.manifest import load_manifest

from libs.bronze_csv import BronzeLoadResult

_FLOE_MANIFEST_ACCESS_MODE_ENV = "OPENLAKEFORGE_FLOE_MANIFEST_ACCESS_MODE"
_FLOE_MANIFEST_ACCESS_MODE_REMOTE = "remote"
_FLOE_MANIFEST_ACCESS_MODE_LOCAL = "local"


@dataclass(frozen=True)
class ProductDefinitionSpec:
    domain: str
    product: str
    asset_prefix: str
    entities: tuple[str, ...]
    gold_assets: tuple[str, ...]
    domain_dir: Path
    bronze_loader: Callable[[], dict[str, BronzeLoadResult]]

    @property
    def job_name(self) -> str:
        return f"{self.asset_prefix}_pipeline"

    @property
    def manifest_path(self) -> Path:
        return (
            self.domain_dir
            / "contracts"
            / "floe"
            / "manifests"
            / f"{self.product}.manifest.json"
        )

    @property
    def dbt_project_dir(self) -> Path:
        return self.domain_dir / "transformations" / "dbt" / self.product

    @property
    def env_key(self) -> str:
        return self.asset_prefix.upper()


def build_product_definitions(spec: ProductDefinitionSpec) -> Definitions:
    floe_manifest_path = _manifest_path_for_dagster(spec)
    floe_manifest_uri = _manifest_uri_for_floe_runner(spec, floe_manifest_path)

    class ProductDbtTranslator(DagsterDbtTranslator):
        def get_asset_key(self, dbt_resource_props) -> AssetKey:
            return AssetKey([spec.asset_prefix, dbt_resource_props["name"]])

        def get_group_name(self, dbt_resource_props) -> str:
            return spec.asset_prefix

    @multi_asset(
        name=f"{spec.asset_prefix}_bronze_sources",
        outs={
            f"{entity}_source": AssetOut(
                key=AssetKey([spec.asset_prefix, f"{entity}_source"]),
                is_required=True,
            )
            for entity in spec.entities
        },
        group_name=spec.asset_prefix,
    )
    def bronze_sources(context):
        results = spec.bronze_loader()
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

    def _dbt_gold_assets(context):
        dbt = DbtCliResource(
            project_dir=str(spec.dbt_project_dir),
            profiles_dir=str(spec.dbt_project_dir),
            target="local_runtime",
        )
        yield from dbt.cli(["build"], context=context).stream()

    _dbt_gold_assets.__name__ = f"{spec.asset_prefix}_dbt_gold_assets"
    dbt_gold_assets = dbt_assets(
        manifest=_ensure_dbt_manifest(spec),
        dagster_dbt_translator=ProductDbtTranslator(),
    )(_dbt_gold_assets)

    product_pipeline = define_asset_job(
        name=spec.job_name,
        selection=(
            AssetSelection.assets(
                *[AssetKey([spec.asset_prefix, f"{entity}_source"]) for entity in spec.entities],
                *[AssetKey([spec.asset_prefix, entity]) for entity in spec.entities],
                *[AssetKey([spec.asset_prefix, asset_name]) for asset_name in spec.gold_assets],
            ).required_multi_asset_neighbors()
        ),
        config=build_job_run_config_from_manifest(floe_manifest_path),
    )

    floe_defs = load_floe_assets(
        manifest_path=floe_manifest_path,
        manifest_uri=floe_manifest_uri,
        runner=build_runner_from_env(),
        register_source_assets=False,
    )

    return Definitions.merge(
        Definitions(
            assets=[
                bronze_sources,
                dbt_gold_assets,
            ],
            jobs=[product_pipeline],
        ),
        floe_defs,
    )


def _ensure_dbt_manifest(spec: ProductDefinitionSpec) -> Path:
    manifest_path = spec.dbt_project_dir / "target" / "manifest.json"
    if manifest_path.exists():
        return manifest_path

    env = os.environ.copy()
    env.setdefault("AWS_ACCESS_KEY_ID", "openlakeforge")
    env.setdefault("AWS_SECRET_ACCESS_KEY", "openlakeforge")
    env.setdefault("OPENLAKEFORGE_STORAGE_REGION", "us-east-1")
    env.setdefault("AWS_REGION", env["OPENLAKEFORGE_STORAGE_REGION"])
    env.setdefault("AWS_DEFAULT_REGION", env["AWS_REGION"])
    env.setdefault("OPENLAKEFORGE_STORAGE_ENDPOINT", "http://seaweedfs-s3:8333")
    env.setdefault("AWS_ENDPOINT_URL_S3", env["OPENLAKEFORGE_STORAGE_ENDPOINT"])
    env.setdefault("OPENLAKEFORGE_DUCKDB_S3_ENDPOINT", env["OPENLAKEFORGE_STORAGE_ENDPOINT"].removeprefix("http://"))
    env.setdefault("OPENLAKEFORGE_DBT_DUCKDB_PATH", f"/tmp/openlakeforge-{spec.asset_prefix}-dbt.duckdb")
    env.setdefault("OPENLAKEFORGE_CATALOG_TYPE", "rest")
    env.setdefault("OPENLAKEFORGE_CATALOG_PROVIDER", "polaris")
    env.setdefault("OPENLAKEFORGE_CATALOG_REST_URI", "http://polaris:8181/api/catalog")
    env.setdefault("OPENLAKEFORGE_CATALOG_TOKEN_URI", "http://polaris:8181/api/catalog/v1/oauth/tokens")
    env.setdefault("OPENLAKEFORGE_CATALOG_WAREHOUSE", "lakehouse_dev")
    env.setdefault("OPENLAKEFORGE_CATALOG_OAUTH_SCOPE", "PRINCIPAL_ROLE:ALL")
    env.setdefault("OPENLAKEFORGE_CATALOG_DBT_CLIENT_ID", "openlakeforge-dbt")
    env.setdefault("OPENLAKEFORGE_CATALOG_DBT_CLIENT_SECRET", "openlakeforge-dbt")
    env.setdefault("OPENLAKEFORGE_CATALOG_SILVER_NAMESPACE", "silver")

    subprocess.run(
        [
            "dbt",
            "deps",
            "--project-dir",
            str(spec.dbt_project_dir),
        ],
        check=True,
        env=env,
    )
    subprocess.run(
        [
            "dbt",
            "parse",
            "--project-dir",
            str(spec.dbt_project_dir),
            "--profiles-dir",
            str(spec.dbt_project_dir),
            "--target",
            "local",
        ],
        check=True,
        env=env,
    )
    return manifest_path


def _manifest_path_for_dagster(spec: ProductDefinitionSpec) -> str:
    if not spec.manifest_path.exists():
        raise RuntimeError(
            f"Missing Floe manifest at {spec.manifest_path}. "
            "Run 'make floe-manifest' before building the project-code image."
        )
    return str(spec.manifest_path)


def _manifest_uri_for_floe_runner(
    spec: ProductDefinitionSpec, manifest_path: str
) -> str | None:
    access_mode = _floe_manifest_access_mode()
    if access_mode == _FLOE_MANIFEST_ACCESS_MODE_REMOTE:
        manifest_uri = _remote_manifest_uri(spec)
        if manifest_uri is None:
            raise RuntimeError(
                f"{_FLOE_MANIFEST_ACCESS_MODE_ENV}=remote for {spec.asset_prefix}, "
                "but no remote Floe manifest URI could be resolved. Set "
                f"OPENLAKEFORGE_FLOE_MANIFEST_URI_{spec.env_key} or "
                "OPENLAKEFORGE_FLOE_MANIFEST_BASE_URI."
            )
        return manifest_uri

    _validate_local_floe_manifest_access(spec, manifest_path)
    return None


def _floe_manifest_access_mode() -> str:
    access_mode = os.environ.get(
        _FLOE_MANIFEST_ACCESS_MODE_ENV,
        _FLOE_MANIFEST_ACCESS_MODE_REMOTE,
    ).strip().lower()
    if access_mode not in {
        _FLOE_MANIFEST_ACCESS_MODE_REMOTE,
        _FLOE_MANIFEST_ACCESS_MODE_LOCAL,
    }:
        raise RuntimeError(
            f"{_FLOE_MANIFEST_ACCESS_MODE_ENV} must be one of "
            f"{_FLOE_MANIFEST_ACCESS_MODE_REMOTE!r} or "
            f"{_FLOE_MANIFEST_ACCESS_MODE_LOCAL!r}; got {access_mode!r}."
        )
    return access_mode


def _validate_local_floe_manifest_access(
    spec: ProductDefinitionSpec, manifest_path: str
) -> None:
    manifest = load_manifest(manifest_path)
    runner_names = {
        entity.runner or manifest.runners.default for entity in manifest.entities
    }
    runner_types: set[str] = set()
    for runner_name in runner_names:
        runner_definition = manifest.runners.definitions.get(runner_name)
        if runner_definition is None:
            raise RuntimeError(
                f"Floe manifest {manifest_path} references unknown runner {runner_name!r}."
            )
        runner_types.add(runner_definition.runner_type)

    non_local_runner_types = sorted(
        runner_type for runner_type in runner_types if runner_type != "local_process"
    )
    if non_local_runner_types:
        raise RuntimeError(
            f"{_FLOE_MANIFEST_ACCESS_MODE_ENV}=local for {spec.asset_prefix}, "
            f"but {manifest_path} uses runner type(s): {', '.join(non_local_runner_types)}. "
            "Local manifest access only works when Floe executes in the same container "
            "as Dagster. Use OPENLAKEFORGE_FLOE_MANIFEST_ACCESS_MODE=remote for "
            "Kubernetes runner pods."
        )


def _remote_manifest_uri(spec: ProductDefinitionSpec) -> str | None:
    specific = os.environ.get(f"OPENLAKEFORGE_FLOE_MANIFEST_URI_{spec.env_key}")
    if specific:
        return specific

    base_uri = os.environ.get("OPENLAKEFORGE_FLOE_MANIFEST_BASE_URI")
    if not base_uri:
        return None

    return f"{base_uri.rstrip('/')}/{spec.domain}/{spec.product}/{spec.product}.manifest.json"
