from __future__ import annotations

import hashlib
import json
import os
import subprocess
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path

import floe_dagster.assets as floe_assets
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
from floe_dagster.definitions import build_job_run_config_from_manifest, build_runner_from_env
from floe_dagster.manifest import load_manifest

from libs.bronze_csv import BronzeLoadResult
from libs.dbt.render_profiles import ensure_runtime_profile_dir
from libs.floe_revision import ArtifactRevisionError, built_manifest_revision, revision_digest as _revision_digest
from libs.openlakeforge_logging import log_event
from libs.s3_artifacts import is_s3_uri, read_json_uri, read_text_uri, upload_file_to_base_uri

_LAKEHOUSE_CODE_ROOT = Path(__file__).resolve().parents[1] / "lakehouse_code"

_FLOE_MANIFEST_ACCESS_MODE_ENV = "OPENLAKEFORGE_FLOE_MANIFEST_ACCESS_MODE"
_FLOE_MANIFEST_ACCESS_MODE_REMOTE = "remote"
_FLOE_MANIFEST_ACCESS_MODE_LOCAL = "local"
_FLOE_S3_REPORT_LOADER_INSTALLED = False


@dataclass(frozen=True)
class ProductDefinitionSpec:
    domain: str
    product: str
    silver_inputs: tuple[str, ...]
    bronze_inputs: tuple[tuple[str, str], ...]
    gold_assets: tuple[str, ...]

    @property
    def job_name(self) -> str:
        return f"{self.product}_pipeline"

    @property
    def dbt_project_dir(self) -> Path:
        return _LAKEHOUSE_CODE_ROOT / "gold" / self.product / "dbt"


@dataclass(frozen=True)
class SourceDefinitionSpec:
    source: str
    resources: tuple[str, ...]
    bronze_loader: Callable[[tuple[str, ...]], dict[str, BronzeLoadResult]]


@dataclass(frozen=True)
class DomainDefinitionSpec:
    domain: str
    tables: tuple[tuple[str, str, str], ...]

    @property
    def manifest_path(self) -> Path:
        return (
            _LAKEHOUSE_CODE_ROOT
            / "silver"
            / self.domain
            / "contracts"
            / "floe"
            / "manifests"
            / f"{self.domain}.manifest.json"
        )

    @property
    def env_key(self) -> str:
        return self.domain.upper()


def build_source_definitions(spec: SourceDefinitionSpec) -> Definitions:
    @multi_asset(
        name=f"{spec.source}_bronze_resources",
        outs={
            resource: AssetOut(
                key=AssetKey([spec.source, resource]),
                # Subset jobs intentionally omit resources owned by the same
                # source. Dagster therefore must not require every output from
                # the shared source multi-asset on each product run.
                is_required=False,
            )
            for resource in spec.resources
        },
        group_name=spec.source,
        can_subset=True,
    )
    def bronze_resources(context):
        previous_run_id = os.environ.get("DAGSTER_RUN_ID")
        os.environ["DAGSTER_RUN_ID"] = context.run_id
        try:
            selected = tuple(
                resource
                for resource in spec.resources
                if context.selected_output_names is None or resource in context.selected_output_names
            )
            results = spec.bronze_loader(selected)
        finally:
            if previous_run_id is None:
                os.environ.pop("DAGSTER_RUN_ID", None)
            else:
                os.environ["DAGSTER_RUN_ID"] = previous_run_id
        for entity, result in results.items():
            log_event(
                context.log,
                message="Loaded Bronze source",
                service="dagster",
                tool="dlt",
                source=spec.source,
                dagster_run_id=context.run_id,
                entity=entity,
                asset_key=f"{spec.source}/{entity}",
                rows=result.rows,
                uri=result.uri,
            )
            yield Output(
                value=None,
                output_name=entity,
                metadata={
                    "rows": MetadataValue.int(result.rows),
                    "bronze_uri": MetadataValue.url(result.uri),
                    "source_file": MetadataValue.path(result.source_file),
                },
            )

    return Definitions(assets=[bronze_resources])


def build_domain_definitions(spec: DomainDefinitionSpec) -> Definitions:
    _install_floe_s3_report_loader()
    floe_manifest_path = _manifest_path_for_dagster(spec)
    floe_manifest_uri = _manifest_uri_for_floe_runner(spec, floe_manifest_path)
    floe_defs = floe_assets.load_floe_assets(
        manifest_path=floe_manifest_path,
        manifest_uri=floe_manifest_uri,
        runner=build_runner_from_env(),
        register_source_assets=False,
    )
    replacements = {
        AssetKey([spec.domain, f"{table}_source"]): AssetKey([source, resource])
        for table, source, resource in spec.tables
    }
    return Definitions(
        assets=[asset.with_attributes(asset_key_replacements=replacements) for asset in floe_defs.assets],
        resources=floe_defs.resources,
    )


def build_product_definitions(spec: ProductDefinitionSpec) -> Definitions:
    class ProductDbtTranslator(DagsterDbtTranslator):
        def get_asset_key(self, dbt_resource_props) -> AssetKey:
            if dbt_resource_props["resource_type"] == "source":
                return AssetKey([spec.domain, dbt_resource_props["name"]])
            return AssetKey([spec.product, dbt_resource_props["name"]])

        def get_group_name(self, dbt_resource_props) -> str:
            return spec.product

    def _dbt_gold_assets(context):
        dbt_target = os.environ.get("OPENLAKEFORGE_DBT_TARGET", "local_runtime")
        dbt_profiles_dir = _ensure_dbt_profiles_dir(spec)
        dbt = DbtCliResource(
            project_dir=str(spec.dbt_project_dir),
            profiles_dir=str(dbt_profiles_dir),
            target=dbt_target,
            dbt_executable=os.environ.get("OPENLAKEFORGE_DBT_EXECUTABLE", "dbt-ol"),
        )
        previous_job_name = os.environ.get("OPENLINEAGE_DBT_JOB_NAME")
        os.environ["OPENLINEAGE_DBT_JOB_NAME"] = spec.job_name
        try:
            yield from dbt.cli(["build"], context=context).stream()
        finally:
            if previous_job_name is None:
                os.environ.pop("OPENLINEAGE_DBT_JOB_NAME", None)
            else:
                os.environ["OPENLINEAGE_DBT_JOB_NAME"] = previous_job_name
        _upload_dbt_artifacts(context, spec)

    _dbt_gold_assets.__name__ = f"{spec.product}_dbt_gold_assets"
    dbt_gold_assets = dbt_assets(
        manifest=_ensure_dbt_manifest(spec),
        dagster_dbt_translator=ProductDbtTranslator(),
    )(_dbt_gold_assets)

    domain_spec = DomainDefinitionSpec(domain=spec.domain, tables=())
    floe_manifest_path = _manifest_path_for_dagster(domain_spec)
    product_pipeline = define_asset_job(
        name=spec.job_name,
        selection=(
            AssetSelection.assets(
                *[AssetKey([source, resource]) for source, resource in spec.bronze_inputs],
                *[AssetKey([spec.domain, entity]) for entity in spec.silver_inputs],
                *[AssetKey([spec.product, asset_name]) for asset_name in spec.gold_assets],
            ).required_multi_asset_neighbors()
        ),
        config=build_job_run_config_from_manifest(floe_manifest_path),
    )

    return Definitions(
        assets=[dbt_gold_assets],
        jobs=[product_pipeline],
    )


def _install_floe_s3_report_loader() -> None:
    global _FLOE_S3_REPORT_LOADER_INSTALLED
    if _FLOE_S3_REPORT_LOADER_INSTALLED:
        return

    original_reader = getattr(floe_assets, "_fsspec_read_text", None)
    if original_reader is not None:
        def _read_remote_text(ref: str) -> str:
            if is_s3_uri(ref):
                return read_text_uri(ref)
            return original_reader(ref)

        floe_assets._fsspec_read_text = _read_remote_text
        _FLOE_S3_REPORT_LOADER_INSTALLED = True
        return

    original_loader = getattr(floe_assets, "_load_local_json_document", None)
    if original_loader is None:
        _FLOE_S3_REPORT_LOADER_INSTALLED = True
        return

    def _load_json_document(ref: str, config_uri: str, *, label: str) -> dict:
        if is_s3_uri(ref):
            return read_json_uri(ref)
        return original_loader(ref, config_uri, label=label)

    floe_assets._load_local_json_document = _load_json_document
    _FLOE_S3_REPORT_LOADER_INSTALLED = True


def _upload_dbt_artifacts(context, spec: ProductDefinitionSpec) -> None:
    base_uri = os.environ.get("OPENLAKEFORGE_RUN_ARTIFACT_BASE_URI")
    if not base_uri:
        context.log.warning("OPENLAKEFORGE_RUN_ARTIFACT_BASE_URI is not set; skipping dbt artifact upload")
        return

    run_id = getattr(context, "run_id", None) or "unknown"
    artifact_candidates = [
        spec.dbt_project_dir / "target" / "manifest.json",
        spec.dbt_project_dir / "target" / "run_results.json",
        spec.dbt_project_dir / "target" / "sources.json",
        spec.dbt_project_dir / "logs" / "dbt.log",
    ]

    for artifact_path in artifact_candidates:
        if not artifact_path.exists():
            continue
        relative_key = (
            f"dbt/{spec.domain}/{spec.product}/{run_id}/"
            f"{artifact_path.relative_to(spec.dbt_project_dir).as_posix()}"
        )
        uri = upload_file_to_base_uri(artifact_path, base_uri=base_uri, relative_key=relative_key)
        log_event(
            context.log,
            message="Uploaded dbt artifact",
            service="dagster",
            tool="dbt",
            domain=spec.domain,
            product=spec.product,
            dagster_run_id=run_id,
            asset_key=f"{spec.product}/dbt_gold_assets",
            artifact=str(artifact_path.relative_to(spec.dbt_project_dir)),
            uri=uri,
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
    env.setdefault("OPENLAKEFORGE_STORAGE_ENDPOINT", "http://seaweedfs-s3.olf-system:8333")
    env.setdefault("AWS_ENDPOINT_URL_S3", env["OPENLAKEFORGE_STORAGE_ENDPOINT"])
    env.setdefault("OPENLAKEFORGE_QUERY_TRINO_HOST", "trino")
    env.setdefault("OPENLAKEFORGE_QUERY_TRINO_PORT", "8080")
    env.setdefault("OPENLAKEFORGE_QUERY_TRINO_CATALOG", "iceberg")
    env.setdefault("OPENLAKEFORGE_CATALOG_NAME", "lakehouse_dev")
    env.setdefault("OPENLAKEFORGE_DBT_TRINO_USER", "openlakeforge-dbt")
    env.setdefault("OPENLAKEFORGE_CATALOG_TYPE", "rest")
    env.setdefault("OPENLAKEFORGE_CATALOG_PROVIDER", "polaris")
    env.setdefault("OPENLAKEFORGE_CATALOG_REST_URI", "http://polaris.olf-system:8181/api/catalog")
    env.setdefault(
        "OPENLAKEFORGE_CATALOG_TOKEN_URI", "http://polaris.olf-system:8181/api/catalog/v1/oauth/tokens"
    )
    env.setdefault("OPENLAKEFORGE_CATALOG_WAREHOUSE", "lakehouse_dev")
    env.setdefault("OPENLAKEFORGE_CATALOG_OAUTH_SCOPE", "PRINCIPAL_ROLE:ALL")
    profiles_dir = _ensure_dbt_profiles_dir(spec, env=env)

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
            str(profiles_dir),
            "--target",
            "local",
        ],
        check=True,
        env=env,
    )
    return manifest_path


def _ensure_dbt_profiles_dir(spec: ProductDefinitionSpec, env: dict[str, str] | None = None) -> Path:
    return ensure_runtime_profile_dir(spec.dbt_project_dir, env=env)


def _manifest_path_for_dagster(spec: DomainDefinitionSpec) -> str:
    if _use_remote_manifest_for_dagster_definitions():
        manifest_uri = _remote_manifest_uri(spec)
        if manifest_uri is None:
            raise RuntimeError(
                f"{_FLOE_MANIFEST_ACCESS_MODE_ENV}=remote for domain {spec.domain}, "
                "but no remote Floe manifest URI could be resolved for Dagster definitions."
            )
        try:
            return _cache_remote_manifest_for_dagster(spec, manifest_uri)
        except ArtifactRevisionError:
            raise
        except RuntimeError:
            if _explicit_floe_dagster_manifest_source() == _FLOE_MANIFEST_ACCESS_MODE_REMOTE:
                raise
            if spec.manifest_path.exists():
                return str(spec.manifest_path)
            raise

    if not spec.manifest_path.exists():
        raise RuntimeError(
            f"Missing Floe manifest at {spec.manifest_path}. "
            "Run 'make floe-manifest' before building the project-code image."
        )
    return str(spec.manifest_path)


def _use_remote_manifest_for_dagster_definitions() -> bool:
    override = _explicit_floe_dagster_manifest_source()
    if override:
        if override not in {"local", "remote"}:
            raise RuntimeError(
                "OPENLAKEFORGE_FLOE_DAGSTER_MANIFEST_SOURCE must be 'local' or 'remote'; "
                f"got {override!r}."
            )
        return override == "remote"

    return _floe_manifest_access_mode() == _FLOE_MANIFEST_ACCESS_MODE_REMOTE


def _explicit_floe_dagster_manifest_source() -> str:
    return os.environ.get("OPENLAKEFORGE_FLOE_DAGSTER_MANIFEST_SOURCE", "").strip().lower()


def _cache_remote_manifest_for_dagster(spec: DomainDefinitionSpec, manifest_uri: str) -> str:
    cache_root = Path(
        os.environ.get("OPENLAKEFORGE_FLOE_MANIFEST_CACHE_DIR", "/tmp/openlakeforge-floe-manifests")
    )
    target = cache_root / spec.domain / f"{spec.domain}.manifest.json"
    target.parent.mkdir(parents=True, exist_ok=True)
    try:
        manifest_content = read_text_uri(manifest_uri)
        revision = built_manifest_revision()
        if revision is not None:
            _verify_revision_manifest(spec, revision, manifest_content)
        target.write_text(manifest_content, encoding="utf-8")
    except ArtifactRevisionError:
        raise
    except Exception as exc:  # noqa: BLE001
        raise RuntimeError(f"Failed to load remote Floe manifest {manifest_uri}") from exc
    return str(target)


def _manifest_uri_for_floe_runner(
    spec: DomainDefinitionSpec, manifest_path: str
) -> str | None:
    access_mode = _floe_manifest_access_mode()
    if access_mode == _FLOE_MANIFEST_ACCESS_MODE_REMOTE:
        manifest_uri = _remote_manifest_uri(spec)
        if manifest_uri is None:
            raise RuntimeError(
                f"{_FLOE_MANIFEST_ACCESS_MODE_ENV}=remote for domain {spec.domain}, "
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
    spec: DomainDefinitionSpec, manifest_path: str
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
            f"{_FLOE_MANIFEST_ACCESS_MODE_ENV}=local for domain {spec.domain}, "
            f"but {manifest_path} uses runner type(s): {', '.join(non_local_runner_types)}. "
            "Local manifest access only works when Floe executes in the same container "
            "as Dagster. Use OPENLAKEFORGE_FLOE_MANIFEST_ACCESS_MODE=remote for "
            "Kubernetes runner pods."
        )


def _remote_manifest_uri(spec: DomainDefinitionSpec) -> str | None:
    revision = built_manifest_revision()
    if revision is not None:
        artifact_base_uri = os.environ.get("OPENLAKEFORGE_ARTIFACT_BASE_URI")
        if not artifact_base_uri:
            raise ArtifactRevisionError(
                "a Floe manifest revision is configured, but "
                "OPENLAKEFORGE_ARTIFACT_BASE_URI is not configured."
            )
        return (
            f"{artifact_base_uri.rstrip('/')}/floe/revisions/sha256/{_revision_digest(revision)}/"
            f"floe/manifests/{spec.domain}/{spec.domain}.manifest.json"
        )

    specific = os.environ.get(f"OPENLAKEFORGE_FLOE_MANIFEST_URI_{spec.env_key}")
    if specific:
        return specific

    base_uri = os.environ.get("OPENLAKEFORGE_FLOE_MANIFEST_BASE_URI")
    if not base_uri:
        return None

    return f"{base_uri.rstrip('/')}/{spec.domain}/{spec.domain}.manifest.json"


def _verify_revision_manifest(
    spec: DomainDefinitionSpec, revision: str, manifest_content: str
) -> None:
    artifact_base_uri = os.environ.get("OPENLAKEFORGE_ARTIFACT_BASE_URI")
    if not artifact_base_uri:
        raise ArtifactRevisionError(
            "a Floe manifest revision is configured, but "
            "OPENLAKEFORGE_ARTIFACT_BASE_URI is not configured."
        )
    sidecar_uri = (
        f"{artifact_base_uri.rstrip('/')}/floe/revisions/sha256/{_revision_digest(revision)}/"
        "REVISION.json"
    )
    try:
        sidecar = json.loads(read_text_uri(sidecar_uri))
        sidecar_revision = sidecar["revision"]
        entries = sidecar["entries"]
    except (json.JSONDecodeError, KeyError, TypeError) as exc:
        raise ArtifactRevisionError(f"malformed immutable revision sidecar {sidecar_uri}") from exc
    except Exception as exc:  # noqa: BLE001
        raise ArtifactRevisionError(f"failed to load immutable revision sidecar {sidecar_uri}") from exc

    if not isinstance(sidecar_revision, str) or not isinstance(entries, dict) or not all(
        isinstance(key, str) and isinstance(digest, str) for key, digest in entries.items()
    ):
        raise ArtifactRevisionError(f"malformed immutable revision sidecar {sidecar_uri}")
    if sidecar_revision != revision:
        raise ArtifactRevisionError(
            f"immutable revision sidecar {sidecar_uri} declares {sidecar_revision!r}, "
            f"not image revision {revision!r}."
        )
    if _aggregate_revision(entries) != revision:
        raise ArtifactRevisionError(
            f"immutable revision sidecar {sidecar_uri} does not aggregate to {revision!r}."
        )

    artifact_key = f"floe/manifests/{spec.domain}/{spec.domain}.manifest.json"
    expected_digest = entries.get(artifact_key)
    actual_digest = hashlib.sha256(manifest_content.encode("utf-8")).hexdigest()
    if expected_digest != actual_digest:
        raise ArtifactRevisionError(
            f"immutable manifest {artifact_key} hashes to {actual_digest}, "
            f"not the revision sidecar value {expected_digest!r}."
        )


def _aggregate_revision(entries: dict[str, str]) -> str:
    if not entries:
        raise ArtifactRevisionError("immutable revision sidecar has no artifact entries.")
    hasher = hashlib.sha256()
    for key in sorted(entries):
        hasher.update(f"{key}\0{entries[key]}\n".encode())
    return f"sha256:{hasher.hexdigest()}"
