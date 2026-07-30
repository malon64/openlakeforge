"""Versioned domain descriptor validation, migration, and discovery helpers.

`discover_domains`/`discover_products` are the single source of truth for
"what products exist". Every consumer that used to hardcode the three seed
products (tools/olf/olf/e2e.py, the Terraform environment roots, and
scripts/test/check-structure.sh) derives its expectations from these
functions instead, so adding a product only requires a new
`domains/<domain>/domain.yaml` (or `olf product scaffold`) and no shared-code
edits.
"""

from __future__ import annotations

import re
from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import yaml

DOMAIN_API_VERSION = "openlakeforge.io/v1alpha1"
DOMAIN_KIND = "Domain"
DOMAINS_DIRNAME = "domains"


class DomainDescriptorError(ValueError):
    """Raised when a domain descriptor is missing or uses an unsupported version."""


def validate_domain_descriptor(document: Mapping[str, Any], *, source: str = "domain.yaml") -> None:
    """Validate the stable envelope and provider-neutral product metadata."""
    if document.get("apiVersion") != DOMAIN_API_VERSION:
        raise DomainDescriptorError(
            f"{source}: unsupported apiVersion {document.get('apiVersion')!r}; expected {DOMAIN_API_VERSION!r}"
        )
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
        for field in ("id", "name", "displayName", "description", "status"):
            if field not in product:
                raise DomainDescriptorError(f"{source}: data_products[{index}] missing {field!r}")
        for field in ("id", "name", "displayName", "status"):
            if not isinstance(product[field], str) or not product[field]:
                raise DomainDescriptorError(
                    f"{source}: data_products[{index}].{field} must be a non-empty string"
                )
        if not isinstance(product["description"], str):
            raise DomainDescriptorError(f"{source}: data_products[{index}].description must be a string")
        if "asset_prefix" in product and (
            not isinstance(product["asset_prefix"], str) or not product["asset_prefix"]
        ):
            raise DomainDescriptorError(f"{source}: data_products[{index}].asset_prefix must be a non-empty string")
        if "domain" in product and (not isinstance(product["domain"], str) or not product["domain"]):
            raise DomainDescriptorError(f"{source}: data_products[{index}].domain must be a non-empty string")
        if "domains" in product and (
            not isinstance(product["domains"], list)
            or not product["domains"]
            or any(not isinstance(domain, str) or not domain for domain in product["domains"])
        ):
            raise DomainDescriptorError(
                f"{source}: data_products[{index}].domains must be a non-empty array of strings"
            )
        if "bronze" in product:
            bronze_entries = product["bronze"]
            if not isinstance(bronze_entries, list):
                raise DomainDescriptorError(f"{source}: data_products[{index}].bronze must be an array")
            for bronze_index, bronze_entry in enumerate(bronze_entries):
                if not isinstance(bronze_entry, Mapping):
                    raise DomainDescriptorError(
                        f"{source}: data_products[{index}].bronze[{bronze_index}] must be an object"
                    )
                for field in ("name", "path"):
                    if not isinstance(bronze_entry.get(field), str) or not bronze_entry[field]:
                        raise DomainDescriptorError(
                            f"{source}: data_products[{index}].bronze[{bronze_index}].{field} "
                            "must be a non-empty string"
                        )
                if "description" in bronze_entry and not isinstance(bronze_entry["description"], str):
                    raise DomainDescriptorError(
                        f"{source}: data_products[{index}].bronze[{bronze_index}].description must be a string"
                    )
        for group in ("silver_tables", "gold_tables"):
            if group not in product:
                continue
            spec = product[group]
            if not isinstance(spec, Mapping):
                raise DomainDescriptorError(f"{source}: data_products[{index}].{group} must be an object")
            tables = spec.get("tables")
            if not isinstance(tables, list):
                raise DomainDescriptorError(f"{source}: data_products[{index}].{group}.tables must be an array")
            for table_index, table in enumerate(tables):
                if not isinstance(table, Mapping) or not isinstance(table.get("name"), str) or not table["name"]:
                    raise DomainDescriptorError(
                        f"{source}: data_products[{index}].{group}.tables[{table_index}] "
                        "must have a non-empty string name"
                    )
                if "fqn" in table or "fullyQualifiedName" in table:
                    raise DomainDescriptorError(
                        f"{source}: data_products[{index}].{group}.tables[{table_index}] "
                        "must not contain physical FQNs"
                    )
                if "description" in table and not isinstance(table["description"], str):
                    raise DomainDescriptorError(
                        f"{source}: data_products[{index}].{group}.tables[{table_index}].description "
                        "must be a string"
                    )
            if "schema" in spec:
                raise DomainDescriptorError(f"{source}: {group}.schema must be derived from provider contracts")
        if "assets" in product and not isinstance(product["assets"], list):
            raise DomainDescriptorError(f"{source}: data_products[{index}].assets must be an array")
        for asset_index, asset in enumerate(product.get("assets") or []):
            if isinstance(asset, str):
                raise DomainDescriptorError(
                    f"{source}: data_products[{index}].assets[{asset_index}] must be a logical asset object"
                )
            if not isinstance(asset, Mapping):
                raise DomainDescriptorError(
                    f"{source}: data_products[{index}].assets[{asset_index}] must be an object"
                )
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
    source = str(path)
    with Path(path).open(encoding="utf-8") as handle:
        document = yaml.safe_load(handle)
    if not isinstance(document, dict):
        raise DomainDescriptorError(f"{source}: descriptor must contain a YAML object")
    validate_domain_descriptor(document, source=source)
    return document


# --- Discovery -----------------------------------------------------------
#
# Physical names below are DERIVED from the logical descriptor using the same
# convention Terraform's environment-root locals use: "<domain>_<product>"
# (or an explicit `asset_prefix`) as the asset prefix, "<prefix>_silver" /
# "<prefix>_gold" as namespace names, "<prefix>_pipeline" as the Dagster job
# name, and "floe/manifests/<domain>/<product>/<product>.manifest.json" as
# the manifest object key. The descriptor validator forbids physical FQNs
# (see `must not contain physical FQNs` above), so this derivation is the
# only place physical identity is computed from the logical contract.


@dataclass(frozen=True)
class ProductRecord:
    """A data product discovered from a domain descriptor, with derived physical names."""

    domain: str
    id: str
    asset_prefix: str
    name: str
    display_name: str
    description: str
    job_name: str
    silver_namespace: str
    gold_namespace: str
    manifest_key: str
    silver_tables: tuple[str, ...]
    gold_tables: tuple[str, ...]
    dashboard_slug: str
    dashboard_title: str


@dataclass(frozen=True)
class DomainRecord:
    """A domain discovered from `domains/<name>/domain.yaml`."""

    name: str
    display_name: str
    path: Path
    products: tuple[ProductRecord, ...]


def _slugify(value: str) -> str:
    return re.sub(r"[^a-z0-9]+", "-", value.lower()).strip("-")


def _asset_prefix(domain_name: str, product: Mapping[str, Any]) -> str:
    explicit = product.get("asset_prefix")
    if isinstance(explicit, str) and explicit:
        return explicit
    return f"{domain_name}_{product['id']}"


def _table_names(product: Mapping[str, Any], group: str) -> tuple[str, ...]:
    spec = product.get(group) or {}
    tables = spec.get("tables") or []
    return tuple(table["name"] for table in tables)


def _product_record(domain_name: str, product: Mapping[str, Any]) -> ProductRecord:
    product_id = product["id"]
    asset_prefix = _asset_prefix(domain_name, product)
    display_name = product.get("displayName") or product.get("display_name") or product_id
    return ProductRecord(
        domain=domain_name,
        id=product_id,
        asset_prefix=asset_prefix,
        name=product.get("name") or asset_prefix,
        display_name=display_name,
        description=product.get("description", ""),
        job_name=f"{asset_prefix}_pipeline",
        silver_namespace=f"{asset_prefix}_silver",
        gold_namespace=f"{asset_prefix}_gold",
        manifest_key=f"floe/manifests/{domain_name}/{product_id}/{product_id}.manifest.json",
        silver_tables=_table_names(product, "silver_tables"),
        gold_tables=_table_names(product, "gold_tables"),
        dashboard_slug=_slugify(display_name),
        dashboard_title=display_name,
    )


def discover_domains(repo_root: str | Path, *, domains_dir: str = DOMAINS_DIRNAME) -> tuple[DomainRecord, ...]:
    """Discover every domain under `<repo_root>/<domains_dir>/*/domain.yaml`.

    Returns an empty tuple when the domains directory does not exist so
    callers (tests, tooling run against a tmp tree) can discover an empty
    platform instead of crashing.
    """
    base = Path(repo_root) / domains_dir
    if not base.is_dir():
        return ()
    records: list[DomainRecord] = []
    for domain_path in sorted(base.glob("*/domain.yaml")):
        document = load_domain_descriptor(domain_path)
        products = tuple(
            _product_record(document["name"], product) for product in document.get("data_products", [])
        )
        records.append(
            DomainRecord(
                name=document["name"],
                display_name=document.get("displayName", document["name"]),
                path=domain_path.parent,
                products=products,
            )
        )
    return tuple(records)


def discover_products(repo_root: str | Path, *, domains_dir: str = DOMAINS_DIRNAME) -> tuple[ProductRecord, ...]:
    """Discover every data product across every domain, flattened."""
    return tuple(
        product
        for domain in discover_domains(repo_root, domains_dir=domains_dir)
        for product in domain.products
    )
