"""Typed, provider-neutral inventory derived from lakehouse descriptors.

The canonical v1alpha3 inventory is composed from the ``Lakehouse`` descriptor
at ``lakehouse_code/lakehouse.yaml`` and one ``Source`` descriptor per bronze
source at ``lakehouse_code/bronze/<source>/source.yaml``.

Canonical identities:

* Bronze  ``<source>.<resource>``
* Silver  ``<domain>.<table>``
* Gold    ``<product>.<table>``

with the dependency chain Source/Bronze -> Domain/Silver -> Product/Gold ->
Dashboard. The legacy v1alpha1/v1alpha2 ``Domain`` inventory loaders below are
preserved for migration diagnostics; the canonical loaders are the
``load_lakehouse_inventory*`` functions.
"""

from __future__ import annotations

import re
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from functools import cache
from pathlib import Path
from typing import Any

from .descriptors import (
    DOMAIN_API_VERSION,
    DomainDescriptorError,
    LakehouseDescriptorError,
    load_domain_descriptor,
    load_lakehouse_descriptor,
    load_source_descriptor,
)

_IDENTIFIER_PATTERN = re.compile(r"^[a-z][a-z0-9_]*$")


@dataclass(frozen=True)
class Table:
    """A provider-neutral logical table or source resource."""

    name: str


@dataclass(frozen=True)
class ArtifactPrefixes:
    """Object-store locations derived from one product's logical identity."""

    manifest_key: str
    floe_report_prefix: str
    dbt_artifact_prefix: str


@dataclass(frozen=True)
class Source:
    """A bronze source and the resources it owns."""

    descriptor_path: Path
    name: str
    display_name: str
    resources: tuple[Table, ...]

    @property
    def bronze_namespace(self) -> str:
        return f"{self.name}_bronze"


@dataclass(frozen=True)
class Product:
    """A v1alpha3 product and its provider-neutral derived identities.

    Product identity is the product ``id`` (also the product ``name``); it is
    globally unique across the lakehouse inventory. The Silver layer is
    domain-aligned (``{domain}_silver``), the Gold layer is product-aligned
    (``{product}_gold``).
    """

    domain_name: str
    id: str
    display_name: str
    source_name: str
    bronze_resources: tuple[Table, ...]
    silver_tables: tuple[Table, ...]
    gold_tables: tuple[Table, ...]

    @property
    def name(self) -> str:
        return self.id

    @property
    def job_name(self) -> str:
        return f"{self.id}_pipeline"

    @property
    def silver_namespace(self) -> str:
        return f"{self.domain_name}_silver"

    @property
    def gold_namespace(self) -> str:
        return f"{self.id}_gold"

    @property
    def definitions_module(self) -> str:
        return f"lakehouse_code.pipelines.dagster.{self.id}"

    @property
    def report_source_dir(self) -> str:
        return f"lakehouse_code/dashboards/superset/{self.id}"

    @property
    def dbt_project_dir(self) -> str:
        return f"lakehouse_code/gold/{self.id}/dbt"

    @property
    def floe_contracts_dir(self) -> str:
        return f"lakehouse_code/silver/{self.domain_name}/contracts/floe"

    @property
    def bronze_object_prefix(self) -> str:
        return self.source_name

    @property
    def openmetadata_data_product_fqns(self) -> tuple[str, str]:
        return self.id, f"{self.domain_name}.{self.id}"

    @property
    def artifact_prefixes(self) -> ArtifactPrefixes:
        return ArtifactPrefixes(
            manifest_key=f"floe/manifests/{self.domain_name}/{self.id}/{self.id}.manifest.json",
            floe_report_prefix=f"floe/reports/{self.domain_name}/{self.id}/",
            dbt_artifact_prefix=f"run-artifacts/dbt/{self.domain_name}/{self.id}/",
        )

    @property
    def superset_export_bundle_name(self) -> str:
        return f"{self.id}_superset_assets_export.zip"

    @property
    def gold_mart_names(self) -> tuple[str, ...]:
        return tuple(f"{self.gold_namespace}.{table.name}" for table in self.gold_tables)


@dataclass(frozen=True)
class Domain:
    """A v1alpha3 domain and its products."""

    descriptor_path: Path
    name: str
    display_name: str
    products: tuple[Product, ...]

    @property
    def silver_namespace(self) -> str:
        return f"{self.name}_silver"


@dataclass(frozen=True)
class Dashboard:
    """A consumption-aligned dashboard bound to one product."""

    name: str
    product: str


@dataclass(frozen=True)
class CatalogNamespace:
    """A physical catalog namespace and its object-store location."""

    name: str
    location: str


@dataclass(frozen=True)
class PhysicalProductNames:
    """Provider-specific names resolved for one logical product."""

    product: str
    silver_namespace: str
    gold_namespace: str
    silver_schema_fqn: str
    gold_schema_fqn: str
    manifest_uri: str


@dataclass(frozen=True)
class PhysicalSourceNames:
    """Provider-specific names resolved for one logical bronze source."""

    source: str
    bronze_namespace: str
    bronze_schema_fqn: str


@dataclass(frozen=True)
class PhysicalInventory:
    """Physical catalog and artifact identities for a provider contract."""

    catalog_namespaces: tuple[CatalogNamespace, ...]
    products: tuple[PhysicalProductNames, ...]
    sources: tuple[PhysicalSourceNames, ...] = ()

    @property
    def silver_namespaces(self) -> dict[str, str]:
        return {product.product: product.silver_namespace for product in self.products}

    @property
    def gold_namespaces(self) -> dict[str, str]:
        return {product.product: product.gold_namespace for product in self.products}

    @property
    def silver_schema_fqns(self) -> dict[str, str]:
        return {product.product: product.silver_schema_fqn for product in self.products}

    @property
    def gold_schema_fqns(self) -> dict[str, str]:
        return {product.product: product.gold_schema_fqn for product in self.products}

    @property
    def bronze_namespaces(self) -> dict[str, str]:
        return {source.source: source.bronze_namespace for source in self.sources}

    @property
    def bronze_schema_fqns(self) -> dict[str, str]:
        return {source.source: source.bronze_schema_fqn for source in self.sources}


@dataclass(frozen=True)
class LakehouseInventory:
    """All sources, domains, products, and dashboards declared in one lakehouse."""

    lakehouse_root: Path
    name: str
    sources: tuple[Source, ...]
    domains: tuple[Domain, ...]
    dashboards: tuple[Dashboard, ...]

    @property
    def products(self) -> tuple[Product, ...]:
        return tuple(product for domain in self.domains for product in domain.products)

    @property
    def job_names(self) -> tuple[str, ...]:
        return tuple(product.job_name for product in self.products)

    @property
    def catalog_namespaces(self) -> tuple[str, ...]:
        return tuple(
            namespace
            for namespace in (*self.bronze_namespace_names, *self.silver_namespace_names, *self.gold_namespace_names)
        )

    @property
    def default_product(self) -> Product:
        if not self.products:
            raise LakehouseDescriptorError(f"{self.lakehouse_root}: no data products were discovered")
        return self.products[0]

    @property
    def artifact_prefixes(self) -> tuple[ArtifactPrefixes, ...]:
        return tuple(product.artifact_prefixes for product in self.products)

    @property
    def domain_names(self) -> tuple[str, ...]:
        return tuple(domain.name for domain in self.domains)

    @property
    def source_names(self) -> tuple[str, ...]:
        return tuple(source.name for source in self.sources)

    @property
    def silver_table_count(self) -> int:
        return sum(len(product.silver_tables) for product in self.products)

    @property
    def gold_table_count(self) -> int:
        return sum(len(product.gold_tables) for product in self.products)

    @property
    def gold_mart_names(self) -> tuple[str, ...]:
        return tuple(mart for product in self.products for mart in product.gold_mart_names)

    @property
    def manifest_keys(self) -> tuple[str, ...]:
        return tuple(product.artifact_prefixes.manifest_key for product in self.products)

    @property
    def openmetadata_data_products(self) -> dict[str, tuple[str, str]]:
        return {product.name: product.openmetadata_data_product_fqns for product in self.products}

    @property
    def bronze_namespace_names(self) -> frozenset[str]:
        return frozenset(source.bronze_namespace for source in self.sources)

    @property
    def silver_namespace_names(self) -> frozenset[str]:
        return frozenset(domain.silver_namespace for domain in self.domains)

    @property
    def gold_namespace_names(self) -> frozenset[str]:
        return frozenset(product.gold_namespace for product in self.products)

    def resolve_physical_names(
        self,
        *,
        catalog_database_fqn: str,
        bronze_bucket: str,
        silver_bucket: str,
        gold_bucket: str,
        manifest_base_uri: str,
    ) -> PhysicalInventory:
        """Resolve physical names from provider-contract values."""
        namespaces: list[CatalogNamespace] = []
        products: list[PhysicalProductNames] = []
        sources: list[PhysicalSourceNames] = []
        for domain in self.domains:
            namespaces.append(
                CatalogNamespace(domain.silver_namespace, f"s3://{silver_bucket}/{domain.silver_namespace}/")
            )
        for product in self.products:
            namespaces.append(
                CatalogNamespace(product.gold_namespace, f"s3://{gold_bucket}/{product.gold_namespace}/")
            )
            products.append(
                PhysicalProductNames(
                    product=product.name,
                    silver_namespace=product.silver_namespace,
                    gold_namespace=product.gold_namespace,
                    silver_schema_fqn=f"{catalog_database_fqn}.{product.silver_namespace}",
                    gold_schema_fqn=f"{catalog_database_fqn}.{product.gold_namespace}",
                    manifest_uri=(
                        f"{manifest_base_uri.rstrip('/')}/{product.domain_name}/{product.id}/{product.id}.manifest.json"
                    ),
                )
            )
        for source in self.sources:
            namespaces.append(
                CatalogNamespace(source.bronze_namespace, f"s3://{bronze_bucket}/{source.bronze_namespace}/")
            )
            sources.append(
                PhysicalSourceNames(
                    source=source.name,
                    bronze_namespace=source.bronze_namespace,
                    bronze_schema_fqn=f"{catalog_database_fqn}.{source.bronze_namespace}",
                )
            )
        return PhysicalInventory(catalog_namespaces=tuple(namespaces), products=tuple(products), sources=tuple(sources))


def _lakehouse_root(path: Path) -> Path:
    candidate = path.resolve()
    return candidate / "lakehouse_code" if (candidate / "lakehouse_code").is_dir() else candidate


def _source_table_names(source: Mapping[str, Any]) -> set[str]:
    resources = source.get("resources")
    if not isinstance(resources, list):
        return set()
    return {resource["name"] for resource in resources if isinstance(resource, Mapping) and "name" in resource}


def _product(path: Path, domain_name: str, product: Mapping[str, Any], index: int, source_names: set[str]) -> Product:
    label = product.get("id") or f"products[{index}]"
    product_id = product.get("id")
    if not isinstance(product_id, str) or not _IDENTIFIER_PATTERN.fullmatch(product_id):
        raise LakehouseDescriptorError(f"{path}: product {label!r}: id must match '^[a-z][a-z0-9_]*$'")
    display_name = product.get("displayName")
    if not isinstance(display_name, str) or not display_name:
        raise LakehouseDescriptorError(f"{path}: product {label!r}: displayName is required and must be a non-empty string")
    bronze = product.get("bronze")
    if not isinstance(bronze, Mapping):
        raise LakehouseDescriptorError(f"{path}: product {label!r}: bronze must be an object with 'source' and 'resources'")
    source_name = bronze.get("source")
    if not isinstance(source_name, str) or source_name not in source_names:
        raise LakehouseDescriptorError(
            f"{path}: product {label!r}: bronze.source {source_name!r} must reference a declared source"
        )
    resources = bronze.get("resources")
    if not isinstance(resources, list) or not resources:
        raise LakehouseDescriptorError(f"{path}: product {label!r}: bronze.resources must be a non-empty array")
    bronze_resources = tuple(
        Table(name=resource) for resource in resources if isinstance(resource, str) and resource
    )
    if len(bronze_resources) != len(resources):
        raise LakehouseDescriptorError(f"{path}: product {label!r}: bronze.resources must contain non-empty string names")
    return Product(
        domain_name=domain_name,
        id=product_id,
        display_name=display_name,
        source_name=source_name,
        bronze_resources=bronze_resources,
        silver_tables=_table_group(path, product, "silver_tables", label),
        gold_tables=_table_group(path, product, "gold_tables", label),
    )


def _table_group(path: Path, product: Mapping[str, Any], field: str, label: str) -> tuple[Table, ...]:
    spec = product.get(field)
    if not isinstance(spec, Mapping):
        raise LakehouseDescriptorError(f"{path}: product {label!r}: {field} is required and must be an object")
    tables = spec.get("tables")
    if not isinstance(tables, list) or not tables:
        raise LakehouseDescriptorError(f"{path}: product {label!r}: {field}.tables must be a non-empty array")
    names = [table.get("name") for table in tables if isinstance(table, Mapping)]
    if len(names) != len(tables) or any(not isinstance(name, str) or not name for name in names):
        raise LakehouseDescriptorError(f"{path}: product {label!r}: {field}.tables must contain non-empty string names")
    if len(names) != len(set(names)):
        raise LakehouseDescriptorError(f"{path}: product {label!r}: {field}.tables must not contain duplicate names")
    return tuple(Table(name=name) for name in names)


def _validate_inventory_identities(
    lakehouse_root: Path,
    sources: tuple[Source, ...],
    domains: tuple[Domain, ...],
    dashboards: tuple[Dashboard, ...],
) -> None:
    seen_sources: set[str] = set()
    for source in sources:
        if source.name in seen_sources:
            raise LakehouseDescriptorError(f"{source.descriptor_path}: duplicate source name {source.name!r}")
        seen_sources.add(source.name)
    seen_domains: set[str] = set()
    seen_product_ids: set[str] = set()
    for domain in domains:
        if domain.name in seen_domains:
            raise LakehouseDescriptorError(f"{domain.descriptor_path}: duplicate domain name {domain.name!r}")
        seen_domains.add(domain.name)
        for product in domain.products:
            if product.id in seen_product_ids:
                raise LakehouseDescriptorError(
                    f"{domain.descriptor_path}: product {product.id!r}: id must be globally unique across the lakehouse"
                )
            seen_product_ids.add(product.id)
            source_tables = {table.name for source in sources if source.name == product.source_name for table in source.resources}
            unknown = [table.name for table in product.bronze_resources if table.name not in source_tables]
            if unknown:
                raise LakehouseDescriptorError(
                    f"{domain.descriptor_path}: product {product.id!r}: bronze.resources reference unknown resources "
                    f"{unknown!r} of source {product.source_name!r}"
                )
    seen_dashboards: set[str] = set()
    for dashboard in dashboards:
        if dashboard.name in seen_dashboards:
            raise LakehouseDescriptorError(f"{lakehouse_root}: duplicate dashboard name {dashboard.name!r}")
        seen_dashboards.add(dashboard.name)
        if dashboard.product not in seen_product_ids:
            raise LakehouseDescriptorError(
                f"{lakehouse_root}: dashboard {dashboard.name!r}: product {dashboard.product!r} must reference a declared product"
            )


def load_lakehouse_inventory_from_descriptors(
    lakehouse_path: str | Path,
    source_paths: Sequence[str | Path],
    *,
    source_label: str | Path | None = None,
) -> LakehouseInventory:
    """Load and validate one Lakehouse descriptor plus its Source descriptors."""
    lakehouse_document = load_lakehouse_descriptor(lakehouse_path)
    sources: list[Source] = []
    for source_path in source_paths:
        document = load_source_descriptor(source_path)
        name = document["name"]
        if name != Path(source_path).parent.name:
            raise LakehouseDescriptorError(
                f"{source_path}: name {name!r} must match source directory {Path(source_path).parent.name!r}"
            )
        resources = tuple(
            Table(name=resource["name"])
            for resource in document["resources"]
            if isinstance(resource, Mapping) and "name" in resource
        )
        sources.append(
            Source(
                descriptor_path=Path(source_path),
                name=name,
                display_name=document["displayName"],
                resources=resources,
            )
        )
    declared_sources = set(lakehouse_document["sources"])
    discovered_sources = {source.name for source in sources}
    if declared_sources != discovered_sources:
        raise LakehouseDescriptorError(
            f"{lakehouse_path}: lakehouse sources {sorted(declared_sources)!r} must match discovered "
            f"source descriptors {sorted(discovered_sources)!r} under lakehouse_code/bronze/*/source.yaml"
        )
    domains: list[Domain] = []
    source_names = {source.name for source in sources}
    for index, domain in enumerate(lakehouse_document["domains"]):
        if not isinstance(domain, Mapping):
            raise LakehouseDescriptorError(f"{lakehouse_path}: domains[{index}] must be an object")
        domain_name = domain["name"]
        products = tuple(
            _product(lakehouse_path, domain_name, product, product_index, source_names)
            for product_index, product in enumerate(domain["products"])
            if isinstance(product, Mapping)
        )
        domains.append(
            Domain(
                descriptor_path=Path(lakehouse_path),
                name=domain_name,
                display_name=domain["displayName"],
                products=products,
            )
        )
    dashboards = tuple(
        Dashboard(name=dashboard["name"], product=dashboard["product"])
        for dashboard in lakehouse_document["dashboards"]
        if isinstance(dashboard, Mapping)
    )
    inventory = LakehouseInventory(
        lakehouse_root=Path(source_label) if source_label is not None else Path(lakehouse_path).parent,
        name=lakehouse_document["name"],
        sources=tuple(sources),
        domains=tuple(domains),
        dashboards=dashboards,
    )
    _validate_inventory_identities(inventory.lakehouse_root, inventory.sources, inventory.domains, inventory.dashboards)
    _ = inventory.default_product
    return inventory


def load_lakehouse_inventory(path: str | Path) -> LakehouseInventory:
    """Load and validate ``lakehouse_code/lakehouse.yaml`` plus every source."""
    lakehouse_root = _lakehouse_root(Path(path))
    lakehouse_descriptor = lakehouse_root / "lakehouse.yaml"
    if not lakehouse_descriptor.is_file():
        raise LakehouseDescriptorError(f"{lakehouse_root}: no lakehouse descriptor found at lakehouse.yaml")
    source_descriptors = sorted(lakehouse_root.glob("bronze/*/source.yaml"))
    if not source_descriptors:
        raise LakehouseDescriptorError(f"{lakehouse_root}: no source descriptors found at bronze/*/source.yaml")
    return load_lakehouse_inventory_from_descriptors(
        lakehouse_descriptor, source_descriptors, source_label=lakehouse_root
    )


@cache
def _cached_lakehouse_inventory(resolved_path: str) -> LakehouseInventory:
    return load_lakehouse_inventory(resolved_path)


def inventory_for(repo_root: str | Path) -> LakehouseInventory:
    """Load and cache the canonical lakehouse inventory for a repository root."""
    return _cached_lakehouse_inventory(str(Path(repo_root).resolve()))


# ---------------------------------------------------------------------------
# Legacy v1alpha1/v1alpha2 domain inventory loaders (migration diagnostics).
# ---------------------------------------------------------------------------


def _legacy_domains_root(path: Path) -> Path:
    candidate = path.resolve()
    return candidate / "domains" if (candidate / "domains").is_dir() else candidate


def _legacy_product_label(product: Mapping[str, Any], index: int) -> str:
    for field in ("id", "name", "asset_prefix"):
        value = product.get(field)
        if isinstance(value, str) and value:
            return value
    return f"data_products[{index}]"


def _legacy_error(path: Path, product: Mapping[str, Any], index: int, field: str, message: str) -> DomainDescriptorError:
    return DomainDescriptorError(f"{path}: product {_legacy_product_label(product, index)!r}: {field} {message}")


def _legacy_required_identifier(path: Path, product: Mapping[str, Any], index: int, field: str) -> str:
    value = product.get(field)
    if not isinstance(value, str) or not value:
        raise _legacy_error(path, product, index, field, "is required and must be a non-empty string")
    if not _IDENTIFIER_PATTERN.fullmatch(value):
        raise _legacy_error(path, product, index, field, "must match '^[a-z][a-z0-9_]*$'")
    return value


def _legacy_table_group(path: Path, product: Mapping[str, Any], index: int, field: str) -> tuple[Table, ...]:
    value = product.get(field)
    if not isinstance(value, Mapping):
        raise _legacy_error(path, product, index, field, "is required and must be an object")
    tables = value.get("tables")
    if not isinstance(tables, list) or not tables:
        raise _legacy_error(path, product, index, f"{field}.tables", "must be a non-empty array")
    names = [table.get("name") for table in tables if isinstance(table, Mapping)]
    if len(names) != len(tables) or any(not isinstance(name, str) or not name for name in names):
        raise _legacy_error(path, product, index, f"{field}.tables", "must contain non-empty string names")
    if len(names) != len(set(names)):
        raise _legacy_error(path, product, index, f"{field}.tables", "must not contain duplicate names")
    return tuple(Table(name=name) for name in names)


def _legacy_bronze_tables(path: Path, product: Mapping[str, Any], index: int) -> tuple[Table, ...]:
    entries = product.get("bronze")
    if not isinstance(entries, list) or not entries:
        raise _legacy_error(path, product, index, "bronze", "is required and must be a non-empty array")
    names = [entry.get("name") for entry in entries if isinstance(entry, Mapping)]
    if len(names) != len(entries) or any(not isinstance(name, str) or not name for name in names):
        raise _legacy_error(path, product, index, "bronze", "must contain non-empty string names")
    if len(names) != len(set(names)):
        raise _legacy_error(path, product, index, "bronze", "must not contain duplicate names")
    return tuple(Table(name=name) for name in names)


class _LegacyProduct:
    """Shape-compatible stand-in for the legacy v1alpha2 product inventory item."""

    def __init__(self, path: Path, domain_name: str, product: Mapping[str, Any], index: int) -> None:
        self.domain_name = domain_name
        self.id = _legacy_required_identifier(path, product, index, "id")
        self.asset_prefix = _legacy_required_identifier(path, product, index, "asset_prefix")
        self.name = product.get("name")
        self.display_name = product.get("displayName")
        if not isinstance(self.name, str) or not self.name:
            raise _legacy_error(path, product, index, "name", "is required and must be a non-empty string")
        if not isinstance(self.display_name, str) or not self.display_name:
            raise _legacy_error(path, product, index, "displayName", "is required and must be a non-empty string")
        self.bronze_tables = _legacy_bronze_tables(path, product, index)
        self.silver_tables = _legacy_table_group(path, product, index, "silver_tables")
        self.gold_tables = _legacy_table_group(path, product, index, "gold_tables")

    @property
    def job_name(self) -> str:
        return f"{self.asset_prefix}_pipeline"

    @property
    def silver_namespace(self) -> str:
        return f"{self.asset_prefix}_silver"

    @property
    def gold_namespace(self) -> str:
        return f"{self.asset_prefix}_gold"

    @property
    def gold_mart_names(self) -> tuple[str, ...]:
        return tuple(f"{self.gold_namespace}.{table.name}" for table in self.gold_tables)

    @property
    def definitions_module(self) -> str:
        return f"domains.{self.domain_name}.pipelines.dagster.{self.id}"

    @property
    def report_source_dir(self) -> str:
        return f"domains/{self.domain_name}/reports/superset/{self.id}"

    @property
    def artifact_prefixes(self) -> ArtifactPrefixes:
        return ArtifactPrefixes(
            manifest_key=f"floe/manifests/{self.domain_name}/{self.id}/{self.id}.manifest.json",
            floe_report_prefix=f"floe/reports/{self.domain_name}/{self.id}/",
            dbt_artifact_prefix=f"run-artifacts/dbt/{self.domain_name}/{self.id}/",
        )


class _LegacyDomain:
    """Shape-compatible stand-in for the legacy v1alpha2 domain inventory item."""

    def __init__(self, descriptor_path: Path, name: str, display_name: str, products: tuple[_LegacyProduct, ...]) -> None:
        self.descriptor_path = descriptor_path
        self.name = name
        self.display_name = display_name
        self.products = products


class _LegacyDomainInventory:
    """Legacy v1alpha2 inventory exposing the old product/domain surface."""

    def __init__(self, domains_root: Path, domains: tuple[_LegacyDomain, ...]) -> None:
        self.domains_root = domains_root
        self.domains = domains

    @property
    def products(self) -> tuple[_LegacyProduct, ...]:
        return tuple(product for domain in self.domains for product in domain.products)

    @property
    def job_names(self) -> tuple[str, ...]:
        return tuple(product.job_name for product in self.products)

    @property
    def catalog_namespaces(self) -> tuple[str, ...]:
        return tuple(
            namespace
            for product in self.products
            for namespace in (product.silver_namespace, product.gold_namespace)
        )

    @property
    def default_product(self) -> _LegacyProduct:
        if not self.products:
            raise DomainDescriptorError(f"{self.domains_root}: no data products were discovered")
        return self.products[0]

    @property
    def artifact_prefixes(self) -> tuple[ArtifactPrefixes, ...]:
        return tuple(product.artifact_prefixes for product in self.products)

    @property
    def domain_names(self) -> tuple[str, ...]:
        return tuple(domain.name for domain in self.domains)

    @property
    def silver_table_count(self) -> int:
        return sum(len(product.silver_tables) for product in self.products)

    @property
    def gold_table_count(self) -> int:
        return sum(len(product.gold_tables) for product in self.products)

    @property
    def gold_mart_names(self) -> tuple[str, ...]:
        return tuple(mart for product in self.products for mart in product.gold_mart_names)

    @property
    def manifest_keys(self) -> tuple[str, ...]:
        return tuple(product.artifact_prefixes.manifest_key for product in self.products)

    @property
    def openmetadata_data_products(self) -> dict[str, tuple[str, str]]:
        return {product.name: product.openmetadata_data_product_fqns for product in self.products}

    @property
    def silver_namespace_names(self) -> frozenset[str]:
        return frozenset(product.silver_namespace for product in self.products)

    @property
    def gold_namespace_names(self) -> frozenset[str]:
        return frozenset(product.gold_namespace for product in self.products)


# Public legacy alias preserved for migration diagnostics and for consumers
# that have not yet been rewired to the canonical LakehouseInventory.
DomainInventory = _LegacyDomainInventory


def _legacy_validate_identities(domains: tuple[_LegacyDomain, ...]) -> None:
    seen_domains: set[str] = set()
    seen_product_names: set[str] = set()
    seen_asset_prefixes: set[str] = set()
    seen_product_ids: set[tuple[str, str]] = set()
    for domain in domains:
        if domain.name in seen_domains:
            raise DomainDescriptorError(f"{domain.descriptor_path}: duplicate domain name {domain.name!r}")
        seen_domains.add(domain.name)
        for product in domain.products:
            identity = (domain.name, product.id)
            if identity in seen_product_ids:
                raise DomainDescriptorError(
                    f"{domain.descriptor_path}: product {product.id!r}: duplicate id within domain {domain.name!r}"
                )
            if product.name in seen_product_names:
                raise DomainDescriptorError(f"{domain.descriptor_path}: duplicate name {product.name!r}")
            if product.asset_prefix in seen_asset_prefixes:
                raise DomainDescriptorError(f"{domain.descriptor_path}: duplicate asset_prefix {product.asset_prefix!r}")
            seen_product_names.add(product.name)
            seen_asset_prefixes.add(product.asset_prefix)
            seen_product_ids.add(identity)


def load_domain_inventory_from_descriptors(
    descriptor_paths: Sequence[str | Path], *, source_label: str | Path, require_directory_match: bool = True
) -> _LegacyDomainInventory:
    """Load and validate an explicit set of legacy domain descriptors."""
    paths = sorted(Path(descriptor_path) for descriptor_path in descriptor_paths)
    if not paths:
        raise DomainDescriptorError(f"{source_label}: no domain descriptors found")
    domains: list[_LegacyDomain] = []
    for descriptor_path in paths:
        document = load_domain_descriptor(descriptor_path)
        domain_name = document["name"]
        if document["apiVersion"] != DOMAIN_API_VERSION:
            raise DomainDescriptorError(
                f"{descriptor_path}: apiVersion {document['apiVersion']!r} must migrate to {DOMAIN_API_VERSION!r}; "
                "see docs/migrations/domain-v1alpha1-to-v1alpha2.md"
            )
        if require_directory_match and domain_name != descriptor_path.parent.name:
            raise DomainDescriptorError(
                f"{descriptor_path}: name {domain_name!r} must match descriptor directory {descriptor_path.parent.name!r}"
            )
        domains.append(
            _LegacyDomain(
                descriptor_path=descriptor_path,
                name=domain_name,
                display_name=document["displayName"],
                products=tuple(
                    _LegacyProduct(descriptor_path, domain_name, product, index)
                    for index, product in enumerate(document["data_products"])
                    if isinstance(product, Mapping)
                ),
            )
        )
    inventory = _LegacyDomainInventory(domains_root=Path(source_label), domains=tuple(domains))
    _legacy_validate_identities(inventory.domains)
    _ = inventory.default_product
    return inventory


def load_domain_inventory(path: str | Path) -> _LegacyDomainInventory:
    """Load and validate every ``domains/*/domain.yaml`` under ``path`` (legacy)."""
    domains_root = _legacy_domains_root(Path(path))
    descriptors = sorted(path for path in domains_root.glob("*/domain.yaml") if path.is_file())
    if not descriptors:
        raise DomainDescriptorError(f"{domains_root}: no domain descriptors found at */domain.yaml")
    return load_domain_inventory_from_descriptors(descriptors, source_label=domains_root)