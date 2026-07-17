"""Versioned domain descriptor validation and migration helpers."""

from __future__ import annotations

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
    if not isinstance(document["data_products"], list):
        raise DomainDescriptorError(f"{source}: data_products must be an array")
    for index, product in enumerate(document["data_products"]):
        if not isinstance(product, Mapping):
            raise DomainDescriptorError(f"{source}: data_products[{index}] must be an object")
        for field in ("id", "name", "displayName", "description", "status"):
            if field not in product:
                raise DomainDescriptorError(f"{source}: data_products[{index}] missing {field!r}")
        for group in ("silver_tables", "gold_tables"):
            spec = product.get(group) or {}
            if "schema" in spec:
                raise DomainDescriptorError(f"{source}: {group}.schema must be derived from provider contracts")
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


def load_domain_descriptor(path: str | Path) -> dict[str, Any]:
    source = str(path)
    with Path(path).open(encoding="utf-8") as handle:
        document = yaml.safe_load(handle)
    if not isinstance(document, dict):
        raise DomainDescriptorError(f"{source}: descriptor must contain a YAML object")
    validate_domain_descriptor(document, source=source)
    return document
