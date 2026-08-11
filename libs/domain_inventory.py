"""Minimal domain-descriptor reader for the project-code runtime.

Duplicates a thin slice of ``tools/olf/olf/inventory.py``: the project-code
image only bakes in ``domains`` and ``libs`` (see
``images/project-code/Dockerfile``), so runtime code cannot import ``olf``.
Kept in sync with ``olf.inventory`` by a parity test in ``tools/olf/tests``.
"""

from __future__ import annotations

import re
from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import yaml

_IDENTIFIER_PATTERN = re.compile(r"^[a-z][a-z0-9_]*$")


class DomainInventoryError(ValueError):
    """Raised when a domain descriptor is missing, malformed, or unresolvable."""


@dataclass(frozen=True)
class Product:
    """A product and the provider-neutral names derived from its descriptor."""

    domain_name: str
    id: str
    name: str
    asset_prefix: str
    bronze_names: tuple[str, ...]
    silver_names: tuple[str, ...]
    gold_names: tuple[str, ...]

    @property
    def job_name(self) -> str:
        return f"{self.asset_prefix}_pipeline"

    @property
    def definitions_module(self) -> str:
        return f"domains.{self.domain_name}.pipelines.dagster.{self.id}"


def _label(product: Mapping[str, Any], index: int) -> str:
    for field in ("id", "name", "asset_prefix"):
        value = product.get(field)
        if isinstance(value, str) and value:
            return value
    return f"data_products[{index}]"


def _error(path: Path, product: Mapping[str, Any], index: int, field: str, message: str) -> DomainInventoryError:
    return DomainInventoryError(f"{path}: product {_label(product, index)!r}: {field} {message}")


def _identifier(path: Path, product: Mapping[str, Any], index: int, field: str) -> str:
    value = product.get(field)
    if not isinstance(value, str) or not value:
        raise _error(path, product, index, field, "is required and must be a non-empty string")
    if not _IDENTIFIER_PATTERN.fullmatch(value):
        raise _error(path, product, index, field, "must match '^[a-z][a-z0-9_]*$'")
    return value


def _names(path: Path, product: Mapping[str, Any], index: int, field: str, *, nested: bool) -> tuple[str, ...]:
    raw = product.get(field)
    entries = raw.get("tables") if nested and isinstance(raw, Mapping) else raw
    location = f"{field}.tables" if nested else field
    if not isinstance(entries, list) or not entries:
        raise _error(path, product, index, location, "must be a non-empty array")
    names: list[str] = []
    for entry_index, entry in enumerate(entries):
        if not isinstance(entry, Mapping) or not isinstance(entry.get("name"), str) or not entry["name"]:
            raise _error(path, product, index, f"{location}[{entry_index}].name", "must be a non-empty string")
        names.append(entry["name"])
    return tuple(names)


def _product(path: Path, domain_name: str, product: Mapping[str, Any], index: int) -> Product:
    product_id = _identifier(path, product, index, "id")
    asset_prefix = _identifier(path, product, index, "asset_prefix")
    name = product.get("name")
    if not isinstance(name, str) or not name:
        raise _error(path, product, index, "name", "is required and must be a non-empty string")
    return Product(
        domain_name=domain_name,
        id=product_id,
        name=name,
        asset_prefix=asset_prefix,
        bronze_names=_names(path, product, index, "bronze", nested=False),
        silver_names=_names(path, product, index, "silver_tables", nested=True),
        gold_names=_names(path, product, index, "gold_tables", nested=True),
    )


def load_domain_products(domains_root: str | Path, domain_name: str) -> tuple[Product, ...]:
    """Load and validate the products declared by one domain's descriptor."""
    path = Path(domains_root) / domain_name / "domain.yaml"
    if not path.is_file():
        raise DomainInventoryError(f"{path}: domain descriptor not found")
    document = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    if not isinstance(document, Mapping):
        raise DomainInventoryError(f"{path}: descriptor must be a YAML mapping")
    raw_products = document.get("data_products")
    if not isinstance(raw_products, list) or not raw_products:
        raise DomainInventoryError(f"{path}: data_products must be a non-empty array")
    return tuple(
        _product(path, domain_name, product, index)
        for index, product in enumerate(raw_products)
        if isinstance(product, Mapping)
    )


def load_all_products(domains_root: str | Path) -> tuple[Product, ...]:
    """Load and validate every domains/*/domain.yaml under domains_root, in stable order."""
    root = Path(domains_root)
    descriptor_paths = sorted(path for path in root.glob("*/domain.yaml") if path.is_file())
    if not descriptor_paths:
        raise DomainInventoryError(f"{root}: no domain descriptors found at */domain.yaml")
    products: list[Product] = []
    for descriptor_path in descriptor_paths:
        products.extend(load_domain_products(root, descriptor_path.parent.name))
    return tuple(products)
