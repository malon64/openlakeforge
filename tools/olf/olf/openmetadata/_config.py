"""OpenMetadata deploy configuration and descriptor source resolution."""

from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path

from openlakeforge_domain import load_domain_inventory_from_descriptors

from olf.clients.openmetadata import OpenMetadataError


@dataclass
class OpenMetadataConfig:
    base_url: str
    admin_email: str
    admin_password: str
    metadata_root: Path
    metadata_source_dir: str
    allow_missing_assets: bool
    catalog_service: str
    catalog_database: str
    cleanup_legacy_default_database: bool
    catalog_database_fqn: str
    catalog_silver_schema_fqns: dict
    catalog_gold_schema_fqns: dict
    storage_service: str
    storage_display_name: str
    storage_endpoint: str
    storage_region: str
    storage_bronze_bucket: str
    storage_silver_bucket: str
    storage_gold_bucket: str

    @classmethod
    def from_environment(
        cls,
        environ,
        *,
        base_url: str,
        admin_email: str,
        admin_password: str,
        metadata_root: str,
        metadata_source_dir: str,
        allow_missing_assets: bool,
        catalog_service: str,
        catalog_database: str,
        cleanup_legacy_default_database: bool,
    ) -> OpenMetadataConfig:
        catalog_service = catalog_service or "polaris"
        catalog_database = catalog_database or "lakehouse_dev"
        catalog_database_fqn = environ.get(
            "OPENLAKEFORGE_CATALOG_DATABASE_FQN", f"{catalog_service}.{catalog_database}"
        )
        silver_schema_fqns_raw = environ.get("OPENLAKEFORGE_CATALOG_SILVER_SCHEMA_FQNS_JSON")
        gold_schema_fqns_raw = environ.get("OPENLAKEFORGE_CATALOG_GOLD_SCHEMA_FQNS_JSON")
        return cls(
            base_url=base_url.rstrip("/"),
            admin_email=admin_email,
            admin_password=admin_password,
            metadata_root=Path(metadata_root),
            metadata_source_dir=metadata_source_dir,
            allow_missing_assets=allow_missing_assets,
            catalog_service=catalog_service,
            catalog_database=catalog_database,
            cleanup_legacy_default_database=cleanup_legacy_default_database,
            catalog_database_fqn=catalog_database_fqn,
            catalog_silver_schema_fqns=(
                _parse_json_env("OPENLAKEFORGE_CATALOG_SILVER_SCHEMA_FQNS_JSON", silver_schema_fqns_raw)
                if silver_schema_fqns_raw
                else _default_schema_fqns(metadata_root, metadata_source_dir, catalog_database_fqn, "silver")
            ),
            catalog_gold_schema_fqns=(
                _parse_json_env("OPENLAKEFORGE_CATALOG_GOLD_SCHEMA_FQNS_JSON", gold_schema_fqns_raw)
                if gold_schema_fqns_raw
                else _default_schema_fqns(metadata_root, metadata_source_dir, catalog_database_fqn, "gold")
            ),
            storage_service=environ.get("OPENLAKEFORGE_STORAGE_OM_SERVICE", "seaweedfs"),
            storage_display_name=environ.get("OPENLAKEFORGE_STORAGE_DISPLAY_NAME", "SeaweedFS S3"),
            storage_endpoint=environ.get("OPENLAKEFORGE_STORAGE_ENDPOINT", "http://seaweedfs-s3:8333"),
            storage_region=environ.get("OPENLAKEFORGE_STORAGE_REGION", "us-east-1"),
            storage_bronze_bucket=environ.get(
                "OPENLAKEFORGE_STORAGE_BRONZE_BUCKET",
                environ.get("OPENLAKEFORGE_STORAGE_BUCKET", "lakehouse-bronze"),
            ),
            storage_silver_bucket=environ.get("OPENLAKEFORGE_STORAGE_SILVER_BUCKET", "lakehouse-silver"),
            storage_gold_bucket=environ.get("OPENLAKEFORGE_STORAGE_GOLD_BUCKET", "lakehouse-gold"),
        )


def _parse_json_env(name: str, raw: str) -> dict:
    try:
        value = json.loads(raw or "{}")
    except json.JSONDecodeError as exc:
        raise OpenMetadataError(f"Environment variable {name} must be valid JSON: {exc}") from exc
    if not isinstance(value, dict):
        raise OpenMetadataError(f"Environment variable {name} must contain a JSON object.")
    return value


def resolve_metadata_descriptor_paths(
    metadata_root: Path, metadata_source_dir: str
) -> tuple[list[Path], Path, bool]:
    """Resolve the descriptors an OpenMetadata deploy will actually read.

    ``metadata_source_dir`` may name a single ``domain.yaml`` file, a single
    domain's directory, or a multi-domain root -- the same three shapes
    :meth:`OpenMetadataDeployer.domain_files` accepts. Returns
    ``(descriptor_paths, source_label, require_directory_match)`` so callers
    computing the schema-FQN defaults use exactly the descriptors that will
    be deployed, not ``metadata_root`` unconditionally. The single-file and
    single-domain-directory shapes name an arbitrary parent directory (for
    example a mounted ``/metadata/domain.yaml``), so ``require_directory_match``
    is ``False`` for those.
    """
    if metadata_source_dir:
        path = Path(metadata_source_dir)
        if not path.exists():
            raise OpenMetadataError(f"OpenMetadata metadata source does not exist: {path}")
        if path.is_file():
            return [path], path, False
        if (path / "domain.yaml").is_file():
            return [path / "domain.yaml"], path, False
        return sorted(path.glob("*/domain.yaml")), path, True

    if not metadata_root.exists():
        raise OpenMetadataError(f"OpenMetadata metadata root does not exist: {metadata_root}")
    return sorted(p for p in metadata_root.glob("*/domain.yaml") if p.is_file()), metadata_root, True


def _default_schema_fqns(
    metadata_root: str, metadata_source_dir: str, catalog_database_fqn: str, layer: str
) -> dict[str, str]:
    """Derive the default schema FQN contract from the domain inventory.

    Used for direct local CLI execution, where the contract environment has
    not resolved OPENLAKEFORGE_CATALOG_{SILVER,GOLD}_SCHEMA_FQNS_JSON. Reads
    the same descriptors resolve_metadata_descriptor_paths() will actually
    deploy, so an OPENMETADATA_METADATA_SOURCE_DIR override that names a
    different product set still gets matching defaults.
    """
    descriptor_paths, source_label, require_directory_match = resolve_metadata_descriptor_paths(
        Path(metadata_root), metadata_source_dir
    )
    inventory = load_domain_inventory_from_descriptors(
        descriptor_paths, source_label=source_label, require_directory_match=require_directory_match
    )
    physical = inventory.resolve_physical_names(
        catalog_database_fqn=catalog_database_fqn,
        silver_bucket="",
        gold_bucket="",
        manifest_base_uri="",
    )
    return physical.silver_schema_fqns if layer == "silver" else physical.gold_schema_fqns
