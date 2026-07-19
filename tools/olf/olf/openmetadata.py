"""OpenMetadata governance metadata seeding over the REST API.

Port of scripts/local/artifacts/openmetadata-metadata-deploy.sh (a bash wrapper
around an embedded Python REST client). The seeding is imperative today; see
docs/technical-debt.md for the reconciliation follow-up. Behavior is preserved
exactly — only the host moved from a heredoc into this shared module.
"""

from __future__ import annotations

import base64
import json
import time
import urllib.error
import urllib.parse
import urllib.request
from collections.abc import Iterator
from dataclasses import dataclass, field
from pathlib import Path

from olf.descriptors import load_domain_descriptor

_SEED_PRODUCT_KEYS = (
    "sales_order_revenue",
    "sales_customer_health",
    "supply_chain_inventory_reliability",
)


class OpenMetadataError(RuntimeError):
    pass


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
                else _default_schema_fqns(catalog_database_fqn, "silver")
            ),
            catalog_gold_schema_fqns=(
                _parse_json_env("OPENLAKEFORGE_CATALOG_GOLD_SCHEMA_FQNS_JSON", gold_schema_fqns_raw)
                if gold_schema_fqns_raw
                else _default_schema_fqns(catalog_database_fqn, "gold")
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


def _default_schema_fqns(catalog_database_fqn: str, layer: str) -> dict[str, str]:
    """Return the seed-product contract used by direct local CLI execution."""
    return {product: f"{catalog_database_fqn}.{product}_{layer}" for product in _SEED_PRODUCT_KEYS}


@dataclass
class OpenMetadataClient:
    base_url: str
    token: str | None = field(default=None)

    def request(
        self,
        method: str,
        path: str,
        *,
        payload=None,
        ok_statuses: tuple[int, ...] = (200,),
        content_type: str = "application/json",
    ):
        data = None
        headers = {"Accept": "application/json"}
        if payload is not None:
            data = json.dumps(payload).encode("utf-8")
            headers["Content-Type"] = content_type
        if self.token:
            headers["Authorization"] = f"Bearer {self.token}"

        req = urllib.request.Request(f"{self.base_url}{path}", data=data, method=method, headers=headers)
        try:
            with urllib.request.urlopen(req, timeout=30) as response:  # noqa: S310 - localhost forward
                body = response.read().decode("utf-8")
                status = response.status
        except urllib.error.HTTPError as err:
            body = err.read().decode("utf-8", errors="replace")
            status = err.code
        except urllib.error.URLError as err:
            raise OpenMetadataError(f"{method} {path} failed: {err}") from err

        if status not in ok_statuses:
            raise OpenMetadataError(f"{method} {path} failed with HTTP {status}: {body}")
        if not body:
            return {}
        try:
            return json.loads(body)
        except json.JSONDecodeError:
            return {"raw": body}


def display_name_from_name(name: str) -> str:
    return " ".join(part.capitalize() for part in name.replace("-", " ").replace("_", " ").split())


def domain_description(domain: dict) -> str:
    parts = []
    if domain.get("description"):
        parts.append(str(domain["description"]))
    if domain.get("status"):
        parts.append(f"Status: {domain['status']}")
    medallion = domain.get("medallion")
    if isinstance(medallion, dict) and medallion:
        parts.append("Medallion layers:")
        for layer, config in medallion.items():
            if isinstance(config, dict):
                owner = config.get("owner", "unknown")
                description = config.get("description", "")
                parts.append(f"- {layer}: {description} Owner: {owner}.")
    return "\n".join(parts)


def domain_payload(domain: dict) -> dict:
    name = domain.get("name")
    if not name:
        raise OpenMetadataError("OpenMetadata domain metadata is missing required field 'name'.")
    payload = {
        "name": name,
        "displayName": domain.get("displayName") or domain.get("display_name") or display_name_from_name(name),
        "domainType": domain.get("domainType") or domain.get("domain_type") or "Source-aligned",
        "description": domain_description(domain),
    }
    for optional in ["owners", "experts", "reviewers", "tags", "style", "extension"]:
        if domain.get(optional):
            payload[optional] = domain[optional]
    return payload


def product_payload(product: dict) -> dict:
    name = product.get("name")
    if not name:
        raise OpenMetadataError("OpenMetadata data-product metadata is missing required field 'name'.")
    domains = product.get("domains")
    if domains is None and product.get("domain"):
        domains = [product["domain"]]
    if not domains:
        raise OpenMetadataError(f"OpenMetadata data product '{name}' must define 'domain' or 'domains'.")
    payload = {
        "name": name,
        "displayName": product.get("displayName") or product.get("display_name") or display_name_from_name(name),
        "description": product.get("description") or "",
        "domains": domains,
    }
    for optional in ["owners", "experts", "reviewers", "tags", "style", "extension"]:
        if product.get(optional):
            payload[optional] = product[optional]
    return payload


def product_entries(domain: dict) -> Iterator[dict]:
    products = domain.get("data_products") or []
    if not isinstance(products, list):
        raise OpenMetadataError(f"Domain '{domain.get('name', '<unknown>')}' data_products must be a list.")
    for product in products:
        if not isinstance(product, dict):
            raise OpenMetadataError(f"Unsupported data product entry in domain '{domain.get('name')}': {product!r}")
        if not product.get("name"):
            product["name"] = f"{domain['name']}_{product.get('id', '')}".rstrip("_")
        if not product.get("domain") and not product.get("domains"):
            product["domain"] = domain["name"]
        yield product


def product_contract_key(product: dict) -> str:
    return product.get("asset_prefix") or product.get("name") or product.get("id") or ""


class OpenMetadataDeployer:
    """Stateful seeding driver; mirrors the original module functions."""

    def __init__(self, config: OpenMetadataConfig, client: OpenMetadataClient):
        self.config = config
        self.client = client

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

    def validate_provider_schema_coverage(self, domain_specs: list[tuple[Path, dict]]) -> None:
        """Fail before metadata writes when descriptors outpace provider contract namespaces."""
        for _, domain in domain_specs:
            for product in product_entries(domain):
                list(self.product_table_specs(product))

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

    def product_asset_entries(self, product: dict):
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

    # --- source discovery -------------------------------------------------

    def domain_files(self):
        if self.config.metadata_source_dir:
            path = Path(self.config.metadata_source_dir)
            if not path.exists():
                raise OpenMetadataError(f"OpenMetadata metadata source does not exist: {path}")
            if path.is_file():
                return [path]
            if (path / "domain.yaml").is_file():
                return [path / "domain.yaml"]
            return sorted(path.glob("*/domain.yaml"))

        if not self.config.metadata_root.exists():
            raise OpenMetadataError(f"OpenMetadata metadata root does not exist: {self.config.metadata_root}")
        return sorted(path for path in self.config.metadata_root.glob("*/domain.yaml") if path.is_file())

    # --- REST operations --------------------------------------------------

    def login(self) -> None:
        encoded_password = base64.b64encode(self.config.admin_password.encode("utf-8")).decode("ascii")
        response = self.client.request(
            "POST",
            "/api/v1/users/login",
            payload={"email": self.config.admin_email, "password": encoded_password},
        )
        token = response.get("accessToken")
        if not token:
            raise OpenMetadataError(f"OpenMetadata login did not return an access token: {response}")
        self.client.token = token

    def wait_for_openmetadata(self) -> None:
        last_error = None
        for _ in range(120):
            try:
                self.client.request("GET", "/api/v1/system/config/jwks")
                return
            except OpenMetadataError as exc:
                last_error = exc
                time.sleep(2)
        raise OpenMetadataError(f"OpenMetadata did not become reachable: {last_error}")

    def resolve_table_asset(self, asset):
        if isinstance(asset, str):
            asset_type = "table"
            fqn = asset
        elif isinstance(asset, dict):
            asset_type = asset.get("type", "table")
            fqn = asset.get("fqn") or asset.get("fullyQualifiedName")
        else:
            raise OpenMetadataError(f"Unsupported asset entry: {asset!r}")

        if asset_type != "table":
            raise OpenMetadataError(
                f"Unsupported OpenMetadata data-product asset type '{asset_type}'. Only 'table' is supported."
            )
        if not fqn:
            raise OpenMetadataError(f"OpenMetadata data-product asset is missing 'fqn': {asset!r}")

        encoded_fqn = urllib.parse.quote(fqn, safe="")
        try:
            table = self.client.request("GET", f"/api/v1/tables/name/{encoded_fqn}?fields=domains")
        except OpenMetadataError as exc:
            if "HTTP 404" in str(exc):
                return None, fqn
            raise

        table_id = table.get("id")
        if not table_id:
            raise OpenMetadataError(f"OpenMetadata table lookup for '{fqn}' did not return an id: {table}")
        return {
            "id": table_id,
            "type": "table",
            "name": table.get("name"),
            "fullyQualifiedName": table.get("fullyQualifiedName", fqn),
            "displayName": table.get("displayName"),
            "domains": table.get("domains") or [],
        }, None

    def resolve_domain_ref(self, domain_name):
        encoded_name = urllib.parse.quote(domain_name, safe="")
        domain = self.client.request("GET", f"/api/v1/domains/name/{encoded_name}")
        domain_id = domain.get("id")
        if not domain_id:
            raise OpenMetadataError(f"OpenMetadata domain lookup for '{domain_name}' did not return an id: {domain}")
        return {
            "id": domain_id,
            "type": "domain",
            "name": domain.get("name", domain_name),
            "fullyQualifiedName": domain.get("fullyQualifiedName", domain_name),
            "displayName": domain.get("displayName"),
        }

    @staticmethod
    def _domain_matches(existing, expected) -> bool:
        return (
            existing.get("id") == expected.get("id")
            or existing.get("fullyQualifiedName") == expected.get("fullyQualifiedName")
            or existing.get("name") == expected.get("name")
        )

    def ensure_table_domains(self, table_refs, domain_refs) -> None:
        for table_ref in table_refs:
            existing_domains = table_ref.get("domains") or []
            missing_domains = [
                domain_ref
                for domain_ref in domain_refs
                if not any(self._domain_matches(existing, domain_ref) for existing in existing_domains)
            ]
            if not missing_domains:
                continue
            domains = existing_domains + missing_domains
            self.client.request(
                "PATCH",
                f"/api/v1/tables/{table_ref['id']}",
                payload=[{"op": "add", "path": "/domains", "value": domains}],
                content_type="application/json-patch+json",
            )
            table_ref["domains"] = domains
            print(
                "Assigned OpenMetadata domain(s) "
                f"{', '.join(domain['fullyQualifiedName'] for domain in missing_domains)} "
                f"to table: {table_ref['fullyQualifiedName']}"
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

    def ensure_storage_service(self) -> None:
        aws_config = {"awsRegion": self.config.storage_region}
        if self.config.storage_endpoint:
            aws_config["endPointURL"] = self.config.storage_endpoint
        payload = {
            "name": self.config.storage_service,
            "displayName": self.config.storage_display_name,
            "serviceType": "S3",
            "connection": {"config": {"type": "S3", "awsConfig": aws_config}},
        }
        self.client.request("PUT", "/api/v1/services/storageServices", payload=payload, ok_statuses=(200, 201))
        print(f"Upserted OpenMetadata storage service: {self.config.storage_service}")

    def ensure_container(self, name, parent_fqn, full_path, description) -> None:
        payload = {
            "name": name,
            "service": self.config.storage_service,
            "fullPath": full_path,
            "description": description,
        }
        if parent_fqn:
            encoded = urllib.parse.quote(parent_fqn, safe="")
            parent = self.client.request("GET", f"/api/v1/containers/name/{encoded}")
            parent_id = parent.get("id")
            if not parent_id:
                raise OpenMetadataError(
                    f"OpenMetadata container lookup for '{parent_fqn}' did not return an id: {parent}"
                )
            payload["parent"] = {"id": parent_id, "type": "container"}
        self.client.request("PUT", "/api/v1/containers", payload=payload, ok_statuses=(200, 201))
        print(f"Upserted OpenMetadata container: {full_path}")

    def ensure_table_stub(self, schema_fqn, name, description) -> None:
        payload = {"name": name, "databaseSchema": schema_fqn, "columns": []}
        if description:
            payload["description"] = description
        self.client.request("PUT", "/api/v1/tables", payload=payload, ok_statuses=(200, 201))
        print(f"Upserted OpenMetadata table stub: {schema_fqn}.{name}")

    def validate_bronze_entries(self, domain_specs) -> None:
        for _, domain in domain_specs:
            for product in product_entries(domain):
                for container in product.get("bronze") or []:
                    if not container.get("path"):
                        raise OpenMetadataError(
                            f"Data product '{product['name']}' Bronze entry is missing required 'path'."
                        )

    def cleanup_legacy_default_database(self) -> None:
        if not self.config.cleanup_legacy_default_database or self.config.catalog_database == "default":
            return
        target_fqn = self.config.catalog_database_fqn
        legacy_fqn = f"{self.config.catalog_service}.default"
        encoded_target = urllib.parse.quote(target_fqn, safe="")
        encoded_legacy = urllib.parse.quote(legacy_fqn, safe="")

        try:
            self.client.request("GET", f"/api/v1/databases/name/{encoded_target}")
        except OpenMetadataError as exc:
            if "HTTP 404" in str(exc):
                import sys

                print(
                    "WARN: Skipping legacy OpenMetadata database cleanup because "
                    f"target database is missing: {target_fqn}",
                    file=sys.stderr,
                )
                return
            raise

        try:
            legacy = self.client.request("GET", f"/api/v1/databases/name/{encoded_legacy}")
        except OpenMetadataError as exc:
            if "HTTP 404" in str(exc):
                return
            raise

        legacy_id = legacy.get("id")
        if not legacy_id:
            raise OpenMetadataError(f"OpenMetadata database lookup for '{legacy_fqn}' did not return an id: {legacy}")
        self.client.request(
            "DELETE",
            f"/api/v1/databases/{legacy_id}?recursive=true&hardDelete=true",
            ok_statuses=(200, 202, 204),
        )
        print(f"Deleted legacy OpenMetadata database metadata: {legacy_fqn}")

    def deploy(self) -> None:
        self.wait_for_openmetadata()
        self.login()

        domain_specs = [(path, load_domain_descriptor(path)) for path in self.domain_files()]
        if not domain_specs:
            raise OpenMetadataError(
                f"No OpenMetadata domain metadata files found under {self.config.metadata_root}/<domain>/domain.yaml"
            )
        self.validate_provider_schema_coverage(domain_specs)

        # Phase A+B: Object Store service and medallion bucket containers.
        self.ensure_storage_service()
        self.validate_bronze_entries(domain_specs)
        for container in self.storage_bucket_specs():
            self.ensure_container(container["name"], None, container["path"], container["description"])

        # Phase C: Pre-seed Iceberg table stubs before the Polaris crawler runs.
        for _, domain in domain_specs:
            for product in product_entries(domain):
                for schema_fqn, table in self.product_table_specs(product):
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
