"""Deployment sequencing for OpenMetadata governance metadata.

`OpenMetadataDeployer` builds the asset specs a deploy needs (from domain
descriptors and the provider-contract schema FQNs) and sequences the phases
`deploy()` runs in. The REST upsert/resolve primitives it calls are
implemented by `OpenMetadataReconciler` (`olf.openmetadata._reconciliation`);
`OpenMetadataDeployer` exposes the same method names as thin delegators so
existing callers and tests keep patching the deployer instance directly.
"""

from __future__ import annotations

import urllib.parse
from collections.abc import Iterator
from pathlib import Path

from openlakeforge_domain import load_domain_descriptor

from olf.clients.openmetadata import OpenMetadataClient, OpenMetadataError
from olf.openmetadata._config import OpenMetadataConfig, resolve_metadata_descriptor_paths
from olf.openmetadata._payloads import domain_payload, product_contract_key, product_entries, product_payload
from olf.openmetadata._reconciliation import OpenMetadataReconciler


class OpenMetadataDeployer:
    """Stateful seeding driver; mirrors the original module functions."""

    def __init__(self, config: OpenMetadataConfig, client: OpenMetadataClient):
        self.config = config
        self.client = client
        self._reconciler = OpenMetadataReconciler(config, client)

    # --- reconciliation delegators -----------------------------------------

    def login(self) -> None:
        self._reconciler.login()

    def wait_for_openmetadata(self) -> None:
        self._reconciler.wait_for_openmetadata()

    def resolve_table_asset(self, asset):
        return self._reconciler.resolve_table_asset(asset)

    def resolve_domain_ref(self, domain_name):
        return self._reconciler.resolve_domain_ref(domain_name)

    def ensure_table_domains(self, table_refs, domain_refs) -> None:
        self._reconciler.ensure_table_domains(table_refs, domain_refs)

    def ensure_storage_service(self) -> None:
        self._reconciler.ensure_storage_service()

    def ensure_container(self, name, parent_fqn, full_path, description) -> None:
        self._reconciler.ensure_container(name, parent_fqn, full_path, description)

    def ensure_database_schema(self, schema_fqn: str) -> None:
        self._reconciler.ensure_database_schema(schema_fqn)

    def ensure_table_stub(self, schema_fqn, name, description) -> None:
        self._reconciler.ensure_table_stub(schema_fqn, name, description)

    def cleanup_legacy_default_database(self) -> None:
        self._reconciler.cleanup_legacy_default_database()

    # --- product/table spec helpers ---------------------------------------

    def schema_fqn_for_product(self, product: dict, table_group_key: str) -> str | None:
        product_key = product_contract_key(product)
        if table_group_key == "silver_tables":
            return self.config.catalog_silver_schema_fqns.get(product_key)
        if table_group_key == "gold_tables":
            return self.config.catalog_gold_schema_fqns.get(product_key)
        return None

    def product_table_specs(self, product: dict):
        for key in ["silver_tables", "gold_tables"]:
            spec = product.get(key)
            if not spec:
                continue
            schema_fqn = self.schema_fqn_for_product(product, key)
            if not schema_fqn:
                raise OpenMetadataError(
                    f"Data product '{product_contract_key(product)}' table group '{key}' is not covered "
                    "by the provider contract schema FQNs."
                )
            for table in spec.get("tables", []):
                yield schema_fqn, table

    def validate_deployment_inputs(self, domain_specs: list[tuple[Path, dict]]) -> None:
        """Resolve every declared table and logical asset before metadata writes."""
        for _, domain in domain_specs:
            for product in product_entries(domain):
                list(self.product_asset_entries(product))
        self.validate_bronze_entries(domain_specs)

    def provider_asset_fqn(self, product: dict, fqn):
        if not fqn:
            return fqn
        table_name = fqn.rsplit(".", 1)[-1]
        for schema_fqn, table in self.product_table_specs(product):
            if table.get("name") == table_name:
                return f"{schema_fqn}.{table_name}"
        return fqn

    def logical_asset_fqn(self, product: dict, asset: dict) -> str:
        """Resolve a provider-neutral table name through the contract schemas."""
        name = asset.get("name")
        if not name:
            raise OpenMetadataError(f"OpenMetadata table asset is missing 'name' or 'fqn': {asset!r}")
        matches = [
            f"{schema_fqn}.{name}"
            for schema_fqn, table in self.product_table_specs(product)
            if table.get("name") == name
        ]
        if not matches:
            raise OpenMetadataError(
                f"OpenMetadata logical table asset '{name}' is not declared in the product table contract."
            )
        if len(matches) > 1:
            raise OpenMetadataError(f"OpenMetadata logical table asset '{name}' is ambiguous: {matches!r}")
        return matches[0]

    def asset_with_provider_fqn(self, product: dict, asset):
        if isinstance(asset, str):
            return self.provider_asset_fqn(product, asset)
        if isinstance(asset, dict):
            rewritten = dict(asset)
            fqn = rewritten.get("fqn") or rewritten.get("fullyQualifiedName")
            if not fqn and rewritten.get("name"):
                fqn = self.logical_asset_fqn(product, rewritten)
            if fqn:
                rewritten["fqn"] = self.provider_asset_fqn(product, fqn)
                rewritten.pop("fullyQualifiedName", None)
            return rewritten
        return asset

    def product_asset_entries(self, product: dict) -> Iterator[dict]:
        seen = set()
        for schema_fqn, table in self.product_table_specs(product):
            fqn = f"{schema_fqn}.{table['name']}"
            if fqn in seen:
                continue
            seen.add(fqn)
            yield {"type": "table", "fqn": fqn}

        for asset in product.get("assets", []):
            if isinstance(asset, str):
                fqn = asset
            elif isinstance(asset, dict):
                fqn = asset.get("fqn") or asset.get("fullyQualifiedName")
            else:
                fqn = None
            resolved = self.asset_with_provider_fqn(product, asset)
            if isinstance(resolved, dict):
                fqn = resolved.get("fqn")
            else:
                fqn = self.provider_asset_fqn(product, fqn)
            if fqn and fqn in seen:
                continue
            if fqn:
                seen.add(fqn)
            yield resolved

    def storage_bucket_specs(self):
        specs = [
            (self.config.storage_bronze_bucket, "Bronze landing bucket for raw immutable source files."),
            (
                self.config.storage_silver_bucket,
                "Silver bucket for Floe-validated Iceberg tables and validation reports.",
            ),
            (self.config.storage_gold_bucket, "Gold bucket for dbt-owned business marts."),
        ]
        seen = set()
        for name, description in specs:
            if not name or name in seen:
                continue
            seen.add(name)
            yield {"name": name, "path": f"s3://{name}", "description": description}

    def bronze_container_specs(self, domain_specs: list[tuple[Path, dict]]):
        """Build provider-specific Bronze container hierarchies from domain contracts."""
        bucket = self.config.storage_bronze_bucket
        root_fqn = f"{self.config.storage_service}.{bucket}"
        seen = set()

        for _, domain in domain_specs:
            for product in product_entries(domain):
                for source in product.get("bronze") or []:
                    source_path = source["path"]
                    parsed = urllib.parse.urlparse(source_path)
                    if parsed.scheme != "s3":
                        raise OpenMetadataError(
                            f"Data product '{product['name']}' Bronze path must use s3://: {source_path}"
                        )

                    segments = [segment for segment in parsed.path.split("/") if segment]
                    parent_fqn = root_fqn
                    full_path = f"s3://{bucket}"
                    for index, segment in enumerate(segments):
                        full_path = f"{full_path}/{segment}"
                        current_fqn = f"{parent_fqn}.{segment}"
                        if full_path not in seen:
                            is_source = index == len(segments) - 1
                            description = (
                                source.get("description", "") if is_source else f"Bronze source prefix for {full_path}."
                            )
                            yield {
                                "name": segment,
                                "parent_fqn": parent_fqn,
                                "path": full_path,
                                "description": description,
                            }
                            seen.add(full_path)
                        parent_fqn = current_fqn

    # --- source discovery -------------------------------------------------

    def domain_files(self):
        descriptor_paths, _source_label, _require_directory_match = resolve_metadata_descriptor_paths(
            self.config.metadata_root, self.config.metadata_source_dir
        )
        return descriptor_paths

    def validate_bronze_entries(self, domain_specs) -> None:
        for _, domain in domain_specs:
            for product in product_entries(domain):
                bronze_entries = product.get("bronze")
                if bronze_entries is None:
                    continue
                if not isinstance(bronze_entries, list):
                    raise OpenMetadataError(
                        f"Data product '{product['name']}' Bronze entries must be an array."
                    )
                for index, container in enumerate(bronze_entries):
                    if not isinstance(container, dict):
                        raise OpenMetadataError(
                            f"Data product '{product['name']}' Bronze entry at index {index} must be an object."
                        )
                    if not isinstance(container.get("name"), str) or not container["name"]:
                        raise OpenMetadataError(
                            f"Data product '{product['name']}' Bronze entry at index {index} "
                            "is missing required 'name'."
                        )
                    if not isinstance(container.get("path"), str) or not container["path"]:
                        raise OpenMetadataError(
                            f"Data product '{product['name']}' Bronze entry at index {index} "
                            "is missing required 'path'."
                        )

    @staticmethod
    def _data_product_asset_ref(table_ref):
        return {
            "id": table_ref["id"],
            "type": table_ref["type"],
            "name": table_ref.get("name"),
            "fullyQualifiedName": table_ref.get("fullyQualifiedName"),
            "displayName": table_ref.get("displayName"),
        }

    def deploy(self) -> None:
        self.wait_for_openmetadata()
        self.login()

        domain_specs = [(path, load_domain_descriptor(path)) for path in self.domain_files()]
        if not domain_specs:
            raise OpenMetadataError(
                f"No OpenMetadata domain metadata files found under {self.config.metadata_root}/<domain>/domain.yaml"
            )
        self.validate_deployment_inputs(domain_specs)

        # Phase A+B: Object Store service and medallion bucket containers.
        self.ensure_storage_service()
        for container in self.storage_bucket_specs():
            self.ensure_container(container["name"], None, container["path"], container["description"])
        for container in self.bronze_container_specs(domain_specs):
            self.ensure_container(
                container["name"],
                container["parent_fqn"],
                container["path"],
                container["description"],
            )

        # Phase C: Pre-seed Iceberg table stubs before the Polaris crawler runs.
        for _, domain in domain_specs:
            for product in product_entries(domain):
                for schema_fqn, table in self.product_table_specs(product):
                    self.ensure_database_schema(schema_fqn)
                    self.ensure_table_stub(schema_fqn, table["name"], table.get("description", ""))
        self.cleanup_legacy_default_database()

        # Phase D: Upsert domains and data products from governance YAML.
        missing_assets = []
        for _, domain in domain_specs:
            domain_body = domain_payload(domain)
            self.client.request("PUT", "/api/v1/domains", payload=domain_body, ok_statuses=(200, 201))
            print(f"Upserted OpenMetadata domain: {domain_body['name']}")

            for product in product_entries(domain):
                product_body = product_payload(product)
                self.client.request("PUT", "/api/v1/dataProducts", payload=product_body, ok_statuses=(200, 201))
                print(f"Upserted OpenMetadata data product: {product_body['name']}")
                domain_refs = [self.resolve_domain_ref(domain_name) for domain_name in product_body["domains"]]

                refs = []
                for asset in self.product_asset_entries(product):
                    ref, missing_fqn = self.resolve_table_asset(asset)
                    if missing_fqn:
                        missing_assets.append(missing_fqn)
                    else:
                        refs.append(ref)

                if refs:
                    self.ensure_table_domains(refs, domain_refs)
                    product_name = urllib.parse.quote(product_body["name"], safe="")
                    self.client.request(
                        "PUT",
                        f"/api/v1/dataProducts/{product_name}/assets/add",
                        payload={"assets": [self._data_product_asset_ref(ref) for ref in refs], "dryRun": False},
                        ok_statuses=(200, 201),
                    )
                    print(f"Attached {len(refs)} OpenMetadata asset(s) to data product: {product_body['name']}")

        if missing_assets:
            message = "\n".join(f"  - {fqn}" for fqn in sorted(set(missing_assets)))
            guidance = (
                "OpenMetadata table assets are not available yet:\n"
                f"{message}\n"
                "Run the product ETL jobs in Dagster, wait for the catalog metadata ingestion to crawl the catalog, "
                "then rerun 'make openmetadata-metadata-deploy'."
            )
            if self.config.allow_missing_assets:
                import sys

                print(f"WARN: {guidance}", file=sys.stderr)
            else:
                raise OpenMetadataError(guidance)
