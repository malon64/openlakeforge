"""Typed, provider-neutral inventory derived from domain descriptors."""

from __future__ import annotations

import re
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from functools import cache
from pathlib import Path
from typing import Any

from .descriptors import DOMAIN_API_VERSION, DomainDescriptorError, load_domain_descriptor

_IDENTIFIER_PATTERN = re.compile(r"^[a-z][a-z0-9_]*$")


@dataclass(frozen=True)
class Table:
    """A provider-neutral logical table."""

    name: str


@dataclass(frozen=True)
class ArtifactPrefixes:
    """Object-store locations derived from one product's logical identity."""

    manifest_key: str
    floe_report_prefix: str
    dbt_artifact_prefix: str


@dataclass(frozen=True)
class Product:
    """A product and its provider-neutral derived identities."""

    domain_name: str
    id: str
    name: str
    display_name: str
    asset_prefix: str
    bronze_tables: tuple[Table, ...]
    silver_tables: tuple[Table, ...]
    gold_tables: tuple[Table, ...]

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
    def definitions_module(self) -> str:
        return f"domains.{self.domain_name}.pipelines.dagster.{self.id}"

    @property
    def report_source_dir(self) -> str:
        return f"domains/{self.domain_name}/reports/superset/{self.id}"

    @property
    def openmetadata_data_product_fqns(self) -> tuple[str, str]:
        return self.name, f"{self.domain_name}.{self.name}"

    @property
    def artifact_prefixes(self) -> ArtifactPrefixes:
        return ArtifactPrefixes(
            manifest_key=f"floe/manifests/{self.domain_name}/{self.id}/{self.id}.manifest.json",
            floe_report_prefix=f"floe/reports/{self.domain_name}/{self.id}/",
            dbt_artifact_prefix=f"run-artifacts/dbt/{self.domain_name}/{self.id}/",
        )

    @property
    def superset_export_bundle_name(self) -> str:
        return f"{self.name}_superset_assets_export.zip"

    @property
    def gold_mart_names(self) -> tuple[str, ...]:
        return tuple(f"{self.gold_namespace}.{table.name}" for table in self.gold_tables)


@dataclass(frozen=True)
class Domain:
    """A domain descriptor and its products."""

    descriptor_path: Path
    name: str
    display_name: str
    products: tuple[Product, ...]


@dataclass(frozen=True)
class CatalogNamespace:
    """A physical catalog namespace and its object-store location."""

    name: str
    location: str


@dataclass(frozen=True)
class PhysicalProductNames:
    """Provider-specific names resolved for one logical product."""

    asset_prefix: str
    silver_namespace: str
    gold_namespace: str
    silver_schema_fqn: str
    gold_schema_fqn: str
    manifest_uri: str


@dataclass(frozen=True)
class PhysicalInventory:
    """Physical catalog and artifact identities for a provider contract."""

    catalog_namespaces: tuple[CatalogNamespace, ...]
    products: tuple[PhysicalProductNames, ...]

    @property
    def silver_namespaces(self) -> dict[str, str]:
        return {product.asset_prefix: product.silver_namespace for product in self.products}

    @property
    def gold_namespaces(self) -> dict[str, str]:
        return {product.asset_prefix: product.gold_namespace for product in self.products}

    @property
    def silver_schema_fqns(self) -> dict[str, str]:
        return {product.asset_prefix: product.silver_schema_fqn for product in self.products}

    @property
    def gold_schema_fqns(self) -> dict[str, str]:
        return {product.asset_prefix: product.gold_schema_fqn for product in self.products}


@dataclass(frozen=True)
class DomainInventory:
    """All domains and products declared in one repository."""

    domains_root: Path
    domains: tuple[Domain, ...]

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
            for product in self.products
            for namespace in (product.silver_namespace, product.gold_namespace)
        )

    @property
    def default_product(self) -> Product:
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

    def resolve_physical_names(
        self,
        *,
        catalog_database_fqn: str,
        silver_bucket: str,
        gold_bucket: str,
        manifest_base_uri: str,
    ) -> PhysicalInventory:
        """Resolve physical names from provider-contract values."""
        namespaces: list[CatalogNamespace] = []
        products: list[PhysicalProductNames] = []
        for product in self.products:
            namespaces.extend(
                (
                    CatalogNamespace(product.silver_namespace, f"s3://{silver_bucket}/{product.silver_namespace}/"),
                    CatalogNamespace(product.gold_namespace, f"s3://{gold_bucket}/{product.gold_namespace}/"),
                )
            )
            products.append(
                PhysicalProductNames(
                    asset_prefix=product.asset_prefix,
                    silver_namespace=product.silver_namespace,
                    gold_namespace=product.gold_namespace,
                    silver_schema_fqn=f"{catalog_database_fqn}.{product.silver_namespace}",
                    gold_schema_fqn=f"{catalog_database_fqn}.{product.gold_namespace}",
                    manifest_uri=(
                        f"{manifest_base_uri.rstrip('/')}/{product.domain_name}/{product.id}/{product.id}.manifest.json"
                    ),
                )
            )
        return PhysicalInventory(catalog_namespaces=tuple(namespaces), products=tuple(products))


def _domains_root(path: Path) -> Path:
    candidate = path.resolve()
    return candidate / "domains" if (candidate / "domains").is_dir() else candidate


def _product_label(product: Mapping[str, Any], index: int) -> str:
    for field in ("id", "name", "asset_prefix"):
        value = product.get(field)
        if isinstance(value, str) and value:
            return value
    return f"data_products[{index}]"


def _error(path: Path, product: Mapping[str, Any], index: int, field: str, message: str) -> DomainDescriptorError:
    return DomainDescriptorError(f"{path}: product {_product_label(product, index)!r}: {field} {message}")


def _required_identifier(path: Path, product: Mapping[str, Any], index: int, field: str) -> str:
    value = product.get(field)
    if not isinstance(value, str) or not value:
        raise _error(path, product, index, field, "is required and must be a non-empty string")
    if not _IDENTIFIER_PATTERN.fullmatch(value):
        raise _error(path, product, index, field, "must match '^[a-z][a-z0-9_]*$'")
    return value


def _table_group(path: Path, product: Mapping[str, Any], index: int, field: str) -> tuple[Table, ...]:
    value = product.get(field)
    if not isinstance(value, Mapping):
        raise _error(path, product, index, field, "is required and must be an object")
    tables = value.get("tables")
    if not isinstance(tables, list) or not tables:
        raise _error(path, product, index, f"{field}.tables", "must be a non-empty array")
    names = [table.get("name") for table in tables if isinstance(table, Mapping)]
    if len(names) != len(tables) or any(not isinstance(name, str) or not name for name in names):
        raise _error(path, product, index, f"{field}.tables", "must contain non-empty string names")
    if len(names) != len(set(names)):
        raise _error(path, product, index, f"{field}.tables", "must not contain duplicate names")
    return tuple(Table(name=name) for name in names)


def _bronze_tables(path: Path, product: Mapping[str, Any], index: int) -> tuple[Table, ...]:
    entries = product.get("bronze")
    if not isinstance(entries, list) or not entries:
        raise _error(path, product, index, "bronze", "is required and must be a non-empty array")
    names = [entry.get("name") for entry in entries if isinstance(entry, Mapping)]
    if len(names) != len(entries) or any(not isinstance(name, str) or not name for name in names):
        raise _error(path, product, index, "bronze", "must contain non-empty string names")
    if len(names) != len(set(names)):
        raise _error(path, product, index, "bronze", "must not contain duplicate names")
    return tuple(Table(name=name) for name in names)


def _product(path: Path, domain_name: str, product: Mapping[str, Any], index: int) -> Product:
    product_id = _required_identifier(path, product, index, "id")
    asset_prefix = _required_identifier(path, product, index, "asset_prefix")
    name = product.get("name")
    display_name = product.get("displayName")
    if not isinstance(name, str) or not name:
        raise _error(path, product, index, "name", "is required and must be a non-empty string")
    if not isinstance(display_name, str) or not display_name:
        raise _error(path, product, index, "displayName", "is required and must be a non-empty string")
    return Product(
        domain_name=domain_name,
        id=product_id,
        name=name,
        display_name=display_name,
        asset_prefix=asset_prefix,
        bronze_tables=_bronze_tables(path, product, index),
        silver_tables=_table_group(path, product, index, "silver_tables"),
        gold_tables=_table_group(path, product, index, "gold_tables"),
    )


def _validate_inventory_identities(domains: tuple[Domain, ...]) -> None:
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
) -> DomainInventory:
    """Load and validate an explicit set of domain descriptors."""
    paths = sorted(Path(descriptor_path) for descriptor_path in descriptor_paths)
    if not paths:
        raise DomainDescriptorError(f"{source_label}: no domain descriptors found")
    domains: list[Domain] = []
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
            Domain(
                descriptor_path=descriptor_path,
                name=domain_name,
                display_name=document["displayName"],
                products=tuple(
                    _product(descriptor_path, domain_name, product, index)
                    for index, product in enumerate(document["data_products"])
                    if isinstance(product, Mapping)
                ),
            )
        )
    inventory = DomainInventory(domains_root=Path(source_label), domains=tuple(domains))
    _validate_inventory_identities(inventory.domains)
    _ = inventory.default_product
    return inventory


def load_domain_inventory(path: str | Path) -> DomainInventory:
    """Load and validate every ``domains/*/domain.yaml`` under ``path``."""
    domains_root = _domains_root(Path(path))
    descriptors = sorted(path for path in domains_root.glob("*/domain.yaml") if path.is_file())
    if not descriptors:
        raise DomainDescriptorError(f"{domains_root}: no domain descriptors found at */domain.yaml")
    return load_domain_inventory_from_descriptors(descriptors, source_label=domains_root)


@cache
def _cached_domain_inventory(resolved_path: str) -> DomainInventory:
    return load_domain_inventory(resolved_path)


def inventory_for(repo_root: str | Path) -> DomainInventory:
    """Load and cache the inventory for a repository root."""
    return _cached_domain_inventory(str(Path(repo_root).resolve()))
