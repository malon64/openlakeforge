"""Aggregate Dagster definitions for the supply_chain domain, driven by domain.yaml."""

from __future__ import annotations

from importlib import import_module
from pathlib import Path

from dagster import Definitions

from libs.domain_inventory import DomainInventoryError, load_domain_products

_DOMAIN_DIR = Path(__file__).resolve().parent
_DOMAIN_NAME = _DOMAIN_DIR.name


def _domain_definitions() -> list[Definitions]:
    """Load each product's Definitions in the order domain.yaml declares them."""
    definitions = []
    for product in load_domain_products(_DOMAIN_DIR.parent, _DOMAIN_NAME):
        try:
            module = import_module(product.definitions_module)
        except ModuleNotFoundError as exc:
            raise DomainInventoryError(
                f"{_DOMAIN_NAME}/domain.yaml: product {product.id!r} has no Dagster module at "
                f"{product.definitions_module!r}"
            ) from exc
        definitions.append(module.defs)
    return definitions


defs = Definitions.merge(*_domain_definitions())
