"""Versioned domain descriptor validation and migration helpers."""

from __future__ import annotations

import re
from collections.abc import Mapping
from pathlib import Path
from typing import Any

import yaml

DOMAIN_API_VERSION = "openlakeforge.io/v1alpha1"
DOMAIN_KIND = "Domain"


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
