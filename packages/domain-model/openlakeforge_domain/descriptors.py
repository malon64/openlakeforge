"""Versioned descriptor validation for Lakehouse, Source, and legacy Domain kinds.

The canonical v1alpha3 model is the ``Lakehouse`` descriptor at
``lakehouse_code/lakehouse.yaml`` together with one ``Source`` descriptor per
bronze source at ``lakehouse_code/bronze/<source>/source.yaml``. The legacy
v1alpha1/v1alpha2 ``Domain`` envelope is preserved here for migration
diagnostics only.
"""

from __future__ import annotations

import re
from collections.abc import Mapping
from pathlib import Path
from typing import Any

import yaml

DOMAIN_API_VERSION = "openlakeforge.io/v1alpha2"
LEGACY_DOMAIN_API_VERSION = "openlakeforge.io/v1alpha1"
DOMAIN_KIND = "Domain"
LAKEHOUSE_API_VERSION = "openlakeforge.io/v1alpha3"
SOURCE_API_VERSION = "openlakeforge.io/v1alpha3"
LAKEHOUSE_KIND = "Lakehouse"
SOURCE_KIND = "Source"
_PRODUCT_IDENTIFIER_PATTERN = r"[a-z][a-z0-9_]*"
_IDENTIFIER_PATTERN = re.compile(rf"^{_PRODUCT_IDENTIFIER_PATTERN}$")


class DomainDescriptorError(ValueError):
    """Raised when a legacy domain descriptor is missing or malformed."""


class LakehouseDescriptorError(ValueError):
    """Raised when a v1alpha3 lakehouse or source descriptor is invalid."""


def _identifier(value: object, *, field: str, source: str) -> str:
    if not isinstance(value, str) or not _IDENTIFIER_PATTERN.fullmatch(value):
        raise LakehouseDescriptorError(f"{source}: {field} must match '^[a-z][a-z0-9_]*$'")
    return value


def _non_empty_string(value: object, *, field: str, source: str) -> str:
    if not isinstance(value, str) or not value:
        raise LakehouseDescriptorError(f"{source}: {field} must be a non-empty string")
    return value


def _optional_string(value: object, *, field: str, source: str) -> None:
    if value is not None and not isinstance(value, str):
        raise LakehouseDescriptorError(f"{source}: {field} must be a string")


def validate_source_descriptor(document: Mapping[str, Any], *, source: str = "source.yaml") -> None:
    """Validate a v1alpha3 Source descriptor envelope and resource list."""
    if document.get("apiVersion") != SOURCE_API_VERSION:
        raise LakehouseDescriptorError(
            f"{source}: unsupported apiVersion {document.get('apiVersion')!r}; expected {SOURCE_API_VERSION!r}"
        )
    if document.get("kind") != SOURCE_KIND:
        raise LakehouseDescriptorError(f"{source}: kind must be {SOURCE_KIND!r}")
    for field in ("name", "displayName", "description", "status", "resources"):
        if field not in document:
            raise LakehouseDescriptorError(f"{source}: missing required field {field!r}")
    _identifier(document["name"], field="name", source=source)
    _non_empty_string(document["displayName"], field="displayName", source=source)
    _non_empty_string(document["status"], field="status", source=source)
    _optional_string(document["description"], field="description", source=source)
    resources = document["resources"]
    if not isinstance(resources, list) or not resources:
        raise LakehouseDescriptorError(f"{source}: resources must be a non-empty array")
    seen: set[str] = set()
    for index, resource in enumerate(resources):
        if not isinstance(resource, Mapping):
            raise LakehouseDescriptorError(f"{source}: resources[{index}] must be an object")
        if "name" not in resource:
            raise LakehouseDescriptorError(f"{source}: resources[{index}] is missing required field 'name'")
        name = _identifier(resource["name"], field=f"resources[{index}].name", source=source)
        if name in seen:
            raise LakehouseDescriptorError(f"{source}: duplicate resource name {name!r}")
        seen.add(name)
        if "description" in resource and not isinstance(resource["description"], str):
            raise LakehouseDescriptorError(f"{source}: resources[{index}].description must be a string")
        unexpected = set(resource) - {"name", "description"}
        if unexpected:
            raise LakehouseDescriptorError(
                f"{source}: resources[{index}] must not contain {sorted(unexpected)!r} (provider-neutral fields only)"
            )
    unexpected = set(document) - {"apiVersion", "kind", "name", "displayName", "description", "status", "resources"}
    if unexpected:
        raise LakehouseDescriptorError(
            f"{source}: must not contain {sorted(unexpected)!r} (provider-neutral fields only)"
        )


def _validate_bronze_reference(product: Mapping[str, Any], *, source: str) -> None:
    bronze = product.get("bronze")
    if not isinstance(bronze, Mapping):
        raise LakehouseDescriptorError(f"{source}: product {product.get('id')!r}: bronze must be an object")
    if "source" not in bronze or "resources" not in bronze:
        raise LakehouseDescriptorError(
            f"{source}: product {product.get('id')!r}: bronze must declare 'source' and 'resources'"
        )
    _identifier(bronze["source"], field="bronze.source", source=source)
    resources = bronze["resources"]
    if not isinstance(resources, list) or not resources:
        raise LakehouseDescriptorError(f"{source}: product {product.get('id')!r}: bronze.resources must be a non-empty array")
    if len(resources) != len(set(resources)):
        raise LakehouseDescriptorError(f"{source}: product {product.get('id')!r}: bronze.resources must not contain duplicates")
    for resource in resources:
        if not isinstance(resource, str) or not resource:
            raise LakehouseDescriptorError(
                f"{source}: product {product.get('id')!r}: bronze.resources must contain non-empty strings"
            )
    unexpected = set(bronze) - {"source", "resources"}
    if unexpected:
        raise LakehouseDescriptorError(
            f"{source}: product {product.get('id')!r}: bronze must not contain {sorted(unexpected)!r} "
            "(provider-neutral fields only)"
        )


def _validate_table_group(product: Mapping[str, Any], *, group: str, source: str) -> None:
    spec = product.get(group)
    if not isinstance(spec, Mapping):
        raise LakehouseDescriptorError(f"{source}: product {product.get('id')!r}: {group} must be an object")
    tables = spec.get("tables")
    if not isinstance(tables, list) or not tables:
        raise LakehouseDescriptorError(f"{source}: product {product.get('id')!r}: {group}.tables must be a non-empty array")
    seen: set[str] = set()
    for index, table in enumerate(tables):
        if not isinstance(table, Mapping) or not _IDENTIFIER_PATTERN.fullmatch(table.get("name") or ""):
            raise LakehouseDescriptorError(
                f"{source}: product {product.get('id')!r}: {group}.tables[{index}].name must match '^[a-z][a-z0-9_]*$'"
            )
        if table["name"] in seen:
            raise LakehouseDescriptorError(
                f"{source}: product {product.get('id')!r}: {group}.tables must not contain duplicate names"
            )
        seen.add(table["name"])
        if "description" in table and not isinstance(table["description"], str):
            raise LakehouseDescriptorError(f"{source}: product {product.get('id')!r}: {group}.tables[{index}].description must be a string")
        unexpected = set(table) - {"name", "description"}
        if unexpected:
            raise LakehouseDescriptorError(
                f"{source}: product {product.get('id')!r}: {group}.tables[{index}] must not contain "
                f"{sorted(unexpected)!r} (provider-neutral fields only)"
            )
    if "schema" in spec:
        raise LakehouseDescriptorError(
            f"{source}: product {product.get('id')!r}: {group}.schema must be derived from provider contracts"
        )


def _validate_product(product: Mapping[str, Any], *, domain_name: str, source: str) -> None:
    label = product.get("id") or f"product {product!r}"
    for field in ("id", "displayName", "description", "status"):
        if field not in product:
            raise LakehouseDescriptorError(f"{source}: product {label!r}: missing required field {field!r}")
    _identifier(product["id"], field="id", source=f"{source}: product {label!r}")
    _non_empty_string(product["displayName"], field="displayName", source=f"{source}: product {label!r}")
    _non_empty_string(product["status"], field="status", source=f"{source}: product {label!r}")
    _optional_string(product["description"], field="description", source=f"{source}: product {label!r}")
    if "domain" in product and product["domain"] != domain_name:
        raise LakehouseDescriptorError(
            f"{source}: product {label!r}: domain {product['domain']!r} must match enclosing domain {domain_name!r}"
        )
    _validate_bronze_reference(product, source=source)
    _validate_table_group(product, group="silver_tables", source=source)
    _validate_table_group(product, group="gold_tables", source=source)
    unexpected = set(product) - {
        "id", "displayName", "description", "status", "domain", "bronze", "silver_tables", "gold_tables",
    }
    if unexpected:
        raise LakehouseDescriptorError(
            f"{source}: product {label!r} must not contain {sorted(unexpected)!r} (provider-neutral fields only)"
        )


def _validate_domain(domain: Mapping[str, Any], *, source: str) -> None:
    for field in ("name", "displayName", "description", "status", "products"):
        if field not in domain:
            raise LakehouseDescriptorError(f"{source}: domain: missing required field {field!r}")
    _identifier(domain["name"], field="domain.name", source=source)
    _non_empty_string(domain["displayName"], field="domain.displayName", source=source)
    _non_empty_string(domain["status"], field="domain.status", source=source)
    _optional_string(domain["description"], field="domain.description", source=source)
    products = domain["products"]
    if not isinstance(products, list) or not products:
        raise LakehouseDescriptorError(f"{source}: domain {domain['name']!r}: products must be a non-empty array")
    for product in products:
        if not isinstance(product, Mapping):
            raise LakehouseDescriptorError(f"{source}: domain {domain['name']!r}: products must contain objects")
        _validate_product(product, domain_name=domain["name"], source=source)
    unexpected = set(domain) - {"name", "displayName", "description", "status", "products"}
    if unexpected:
        raise LakehouseDescriptorError(
            f"{source}: domain {domain['name']!r} must not contain {sorted(unexpected)!r} (provider-neutral fields only)"
        )


def _validate_dashboard(dashboard: Mapping[str, Any], *, source: str) -> None:
    for field in ("name", "product"):
        if field not in dashboard:
            raise LakehouseDescriptorError(f"{source}: dashboard: missing required field {field!r}")
    _identifier(dashboard["name"], field="dashboard.name", source=source)
    _identifier(dashboard["product"], field="dashboard.product", source=source)
    unexpected = set(dashboard) - {"name", "product"}
    if unexpected:
        raise LakehouseDescriptorError(
            f"{source}: dashboard {dashboard['name']!r} must not contain {sorted(unexpected)!r} "
            "(provider-neutral fields only)"
        )


def validate_lakehouse_descriptor(document: Mapping[str, Any], *, source: str = "lakehouse.yaml") -> None:
    """Validate the v1alpha3 Lakehouse envelope, domains, and dashboards."""
    if document.get("apiVersion") != LAKEHOUSE_API_VERSION:
        raise LakehouseDescriptorError(
            f"{source}: unsupported apiVersion {document.get('apiVersion')!r}; expected {LAKEHOUSE_API_VERSION!r}"
        )
    if document.get("kind") != LAKEHOUSE_KIND:
        raise LakehouseDescriptorError(f"{source}: kind must be {LAKEHOUSE_KIND!r}")
    for field in ("name", "displayName", "description", "status", "sources", "domains", "dashboards"):
        if field not in document:
            raise LakehouseDescriptorError(f"{source}: missing required field {field!r}")
    _identifier(document["name"], field="name", source=source)
    _non_empty_string(document["displayName"], field="displayName", source=source)
    _non_empty_string(document["status"], field="status", source=source)
    _optional_string(document["description"], field="description", source=source)
    sources = document["sources"]
    if not isinstance(sources, list) or not sources:
        raise LakehouseDescriptorError(f"{source}: sources must be a non-empty array")
    for index, source_name in enumerate(sources):
        if not isinstance(source_name, str) or not source_name:
            raise LakehouseDescriptorError(f"{source}: sources[{index}] must be a non-empty string")
    if len(sources) != len(set(sources)):
        raise LakehouseDescriptorError(f"{source}: sources must not contain duplicates")
    domains = document["domains"]
    if not isinstance(domains, list) or not domains:
        raise LakehouseDescriptorError(f"{source}: domains must be a non-empty array")
    for domain in domains:
        if not isinstance(domain, Mapping):
            raise LakehouseDescriptorError(f"{source}: domains must contain objects")
        _validate_domain(domain, source=source)
    dashboards = document["dashboards"]
    if not isinstance(dashboards, list):
        raise LakehouseDescriptorError(f"{source}: dashboards must be an array")
    for dashboard in dashboards:
        if not isinstance(dashboard, Mapping):
            raise LakehouseDescriptorError(f"{source}: dashboards must contain objects")
        _validate_dashboard(dashboard, source=source)
    unexpected = set(document) - {
        "apiVersion", "kind", "name", "displayName", "description", "status", "sources", "domains", "dashboards",
    }
    if unexpected:
        raise LakehouseDescriptorError(
            f"{source}: must not contain {sorted(unexpected)!r} (provider-neutral fields only)"
        )


def load_lakehouse_descriptor(path: str | Path) -> dict[str, Any]:
    """Load and validate the v1alpha3 Lakehouse descriptor at ``path``."""
    source = str(path)
    with Path(path).open(encoding="utf-8") as handle:
        document = yaml.safe_load(handle)
    if not isinstance(document, dict):
        raise LakehouseDescriptorError(f"{source}: descriptor must contain a YAML object")
    validate_lakehouse_descriptor(document, source=source)
    return document


def load_source_descriptor(path: str | Path) -> dict[str, Any]:
    """Load and validate the v1alpha3 Source descriptor at ``path``."""
    source = str(path)
    with Path(path).open(encoding="utf-8") as handle:
        document = yaml.safe_load(handle)
    if not isinstance(document, dict):
        raise LakehouseDescriptorError(f"{source}: descriptor must contain a YAML object")
    validate_source_descriptor(document, source=source)
    return document


def validate_domain_descriptor(document: Mapping[str, Any], *, source: str = "domain.yaml") -> None:
    """Validate the stable envelope and provider-neutral product metadata."""
    api_version = document.get("apiVersion")
    if api_version not in {LEGACY_DOMAIN_API_VERSION, DOMAIN_API_VERSION}:
        raise DomainDescriptorError(
            f"{source}: unsupported apiVersion {api_version!r}; expected {LEGACY_DOMAIN_API_VERSION!r} "
            f"or {DOMAIN_API_VERSION!r}"
        )
    inventory_version = api_version == DOMAIN_API_VERSION
    if document.get("kind") != DOMAIN_KIND:
        raise DomainDescriptorError(f"{source}: kind must be {DOMAIN_KIND!r}")
    for field in ("name", "displayName", "description", "status", "data_products"):
        if field not in document:
            raise DomainDescriptorError(f"{source}: missing required field {field!r}")
    if not isinstance(document["name"], str) or not re.fullmatch(r"[a-z][a-z0-9_]*", document["name"]):
        raise DomainDescriptorError(f"{source}: name must match '^[a-z][a-z0-9_]*$'")
    for field in ("displayName", "status"):
        if not isinstance(document[field], str) or not document[field]:
            raise DomainDescriptorError(f"{source}: {field} must be a non-empty string")
    if not isinstance(document["description"], str):
        raise DomainDescriptorError(f"{source}: description must be a string")
    if not isinstance(document["data_products"], list):
        raise DomainDescriptorError(f"{source}: data_products must be an array")
    for index, product in enumerate(document["data_products"]):
        if not isinstance(product, Mapping):
            raise DomainDescriptorError(f"{source}: data_products[{index}] must be an object")
        product_label = product.get("id") or product.get("name") or f"data_products[{index}]"
        required_fields = ["id", "name", "displayName", "description", "status"]
        if inventory_version:
            required_fields.extend(("asset_prefix", "bronze", "silver_tables", "gold_tables"))
        for field in required_fields:
            if field not in product:
                raise DomainDescriptorError(f"{source}: product {product_label!r}: missing required field {field!r}")
        for field in ("id", "name", "displayName", "status"):
            if not isinstance(product[field], str) or not product[field]:
                raise DomainDescriptorError(f"{source}: product {product_label!r}: {field} must be a non-empty string")
        if inventory_version and not re.fullmatch(_PRODUCT_IDENTIFIER_PATTERN, product["id"]):
            raise DomainDescriptorError(f"{source}: product {product_label!r}: id must match '^[a-z][a-z0-9_]*$'")
        if not isinstance(product["description"], str):
            raise DomainDescriptorError(f"{source}: product {product_label!r}: description must be a string")
        if "asset_prefix" in product:
            if inventory_version and (
                not isinstance(product["asset_prefix"], str)
                or not re.fullmatch(_PRODUCT_IDENTIFIER_PATTERN, product["asset_prefix"])
            ):
                raise DomainDescriptorError(
                    f"{source}: product {product_label!r}: asset_prefix must match '^[a-z][a-z0-9_]*$'"
                )
            if not inventory_version and (not isinstance(product["asset_prefix"], str) or not product["asset_prefix"]):
                raise DomainDescriptorError(
                    f"{source}: product {product_label!r}: asset_prefix must be a non-empty string"
                )
        if "domain" in product and (not isinstance(product["domain"], str) or not product["domain"]):
            raise DomainDescriptorError(f"{source}: data_products[{index}].domain must be a non-empty string")
        if "domains" in product and (
            not isinstance(product["domains"], list)
            or not product["domains"]
            or any(not isinstance(domain, str) or not domain for domain in product["domains"])
        ):
            raise DomainDescriptorError(f"{source}: data_products[{index}].domains must be a non-empty array of strings")
        if "bronze" in product:
            bronze_entries = product["bronze"]
            if not isinstance(bronze_entries, list) or (inventory_version and not bronze_entries):
                requirement = "a non-empty" if inventory_version else "an"
                raise DomainDescriptorError(f"{source}: product {product_label!r}: bronze must be {requirement} array")
            for bronze_index, bronze_entry in enumerate(bronze_entries):
                if not isinstance(bronze_entry, Mapping):
                    raise DomainDescriptorError(
                        f"{source}: product {product_label!r}: bronze[{bronze_index}] must be an object"
                    )
                for field in ("name", "path"):
                    if not isinstance(bronze_entry.get(field), str) or not bronze_entry[field]:
                        raise DomainDescriptorError(
                            f"{source}: product {product_label!r}: bronze[{bronze_index}].{field} must be a non-empty string"
                        )
                if "description" in bronze_entry and not isinstance(bronze_entry["description"], str):
                    raise DomainDescriptorError(
                        f"{source}: product {product_label!r}: bronze[{bronze_index}].description must be a string"
                    )
        for group in ("silver_tables", "gold_tables"):
            if group not in product:
                continue
            spec = product[group]
            if not isinstance(spec, Mapping):
                raise DomainDescriptorError(f"{source}: product {product_label!r}: {group} must be an object")
            tables = spec.get("tables")
            if not isinstance(tables, list) or (inventory_version and not tables):
                requirement = "a non-empty" if inventory_version else "an"
                raise DomainDescriptorError(f"{source}: product {product_label!r}: {group}.tables must be {requirement} array")
            for table_index, table in enumerate(tables):
                if not isinstance(table, Mapping) or not isinstance(table.get("name"), str) or not table["name"]:
                    raise DomainDescriptorError(
                        f"{source}: product {product_label!r}: {group}.tables[{table_index}] must have a non-empty string name"
                    )
                if "fqn" in table or "fullyQualifiedName" in table:
                    raise DomainDescriptorError(
                        f"{source}: product {product_label!r}: {group}.tables[{table_index}] must not contain physical FQNs"
                    )
                if "description" in table and not isinstance(table["description"], str):
                    raise DomainDescriptorError(
                        f"{source}: product {product_label!r}: {group}.tables[{table_index}].description must be a string"
                    )
            if "schema" in spec:
                raise DomainDescriptorError(
                    f"{source}: product {product_label!r}: {group}.schema must be derived from provider contracts"
                )
        if "assets" in product and not isinstance(product["assets"], list):
            raise DomainDescriptorError(f"{source}: data_products[{index}].assets must be an array")
        for asset_index, asset in enumerate(product.get("assets") or []):
            if isinstance(asset, str):
                raise DomainDescriptorError(
                    f"{source}: data_products[{index}].assets[{asset_index}] must be a logical asset object"
                )
            if not isinstance(asset, Mapping):
                raise DomainDescriptorError(f"{source}: data_products[{index}].assets[{asset_index}] must be an object")
            if "fqn" in asset or "fullyQualifiedName" in asset:
                raise DomainDescriptorError(
                    f"{source}: data_products[{index}].assets[{asset_index}] must not contain physical FQNs"
                )
            if not isinstance(asset.get("name"), str) or not asset["name"]:
                raise DomainDescriptorError(
                    f"{source}: data_products[{index}].assets[{asset_index}] must have a non-empty logical name"
                )
            if asset.get("type") not in (None, "table"):
                raise DomainDescriptorError(
                    f"{source}: data_products[{index}].assets[{asset_index}] must have type 'table' when specified"
                )


def load_domain_descriptor(path: str | Path) -> dict[str, Any]:
    """Load one YAML descriptor after validating it."""
    source = str(path)
    with Path(path).open(encoding="utf-8") as handle:
        document = yaml.safe_load(handle)
    if not isinstance(document, dict):
        raise DomainDescriptorError(f"{source}: descriptor must contain a YAML object")
    validate_domain_descriptor(document, source=source)
    return document
