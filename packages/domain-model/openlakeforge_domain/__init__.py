"""Canonical provider-neutral domain descriptors and inventory."""

from __future__ import annotations

from .descriptors import (
    DOMAIN_API_VERSION,
    DOMAIN_KIND,
    LEGACY_DOMAIN_API_VERSION,
    DomainDescriptorError,
    load_domain_descriptor,
    validate_domain_descriptor,
)
from .inventory import (
    ArtifactPrefixes,
    CatalogNamespace,
    Domain,
    DomainInventory,
    PhysicalInventory,
    PhysicalProductNames,
    Product,
    Table,
    inventory_for,
    load_domain_inventory,
    load_domain_inventory_from_descriptors,
)

__all__ = [
    "DOMAIN_API_VERSION",
    "DOMAIN_KIND",
    "LEGACY_DOMAIN_API_VERSION",
    "ArtifactPrefixes",
    "CatalogNamespace",
    "Domain",
    "DomainDescriptorError",
    "DomainInventory",
    "PhysicalInventory",
    "PhysicalProductNames",
    "Product",
    "Table",
    "inventory_for",
    "load_domain_descriptor",
    "load_domain_inventory",
    "load_domain_inventory_from_descriptors",
    "validate_domain_descriptor",
]
